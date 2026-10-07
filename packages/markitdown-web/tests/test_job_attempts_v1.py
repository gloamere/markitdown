"""Durable replay, provenance and attempt lifecycle regression coverage."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from test_job_service import service as service
from test_job_service import update, upload, user_update

from markitdown_web.conversion import ConversionError
from markitdown_web.jobs import JobError, JobService


@pytest.fixture(autouse=True)
def ready(monkeypatch):
    # These tests cover the scheduler boundary, independently of host sandbox support.
    monkeypatch.setattr(
        "markitdown_web.jobs.ensure_engine_available", lambda *args: None
    )
    monkeypatch.setattr(
        "markitdown_web.jobs.validate_engine_uploads", lambda *args: None
    )


def accepted(service, user="alice", key="batch-one", **kwargs):
    return service.enqueue(user, [upload(**kwargs)], submission_key=key)[0]


def assert_error(code, action):
    with pytest.raises(JobError) as error:
        action()
    assert error.value.status_code == code


def test_batch_replay_is_owner_scoped_and_durable(service, monkeypatch):
    files = [upload(filename="one.txt"), upload(data=b"different", filename="two.md")]
    first = service.enqueue("alice", files, submission_key="fixed-key")
    again = JobService(service.db, service.settings)
    monkeypatch.setattr(
        again, "_available", lambda _: pytest.fail("Replay must not rerun readiness")
    )
    assert again.enqueue("alice", files, submission_key="fixed-key") == first
    assert service.get_usage("alice")["used"] == 2
    other = service.enqueue("bob", files, submission_key="fixed-key")
    assert {x["id"] for x in first}.isdisjoint(x["id"] for x in other)
    assert service.get_usage("bob")["used"] == 2
    with service.db.connect() as connection:
        rows = connection.execute("SELECT * FROM job_submissions").fetchall()
    assert len(rows) == 2
    assert all(row["key_hash"] != "fixed-key" for row in rows)


def test_concurrent_submission_replays_reserve_and_charge_only_once(service):
    barrier = threading.Barrier(12)

    def submit(_):
        barrier.wait()
        return accepted(service)["id"]

    with ThreadPoolExecutor(max_workers=12) as pool:
        ids = list(pool.map(submit, range(12)))
    assert len(set(ids)) == 1
    assert service.get_usage("alice")["used"] == 1
    assert len(list(service.jobs_dir.iterdir())) == 1
    assert len(service.list_attempts("alice", ids[0])) == 1


def test_claim_timestamp_follows_submission_committed_while_waiting_for_writer(
    service, monkeypatch
):
    original_transaction = service.db.transaction
    waiting_submission = []
    writer_ahead = True

    @contextmanager
    def transaction_after_submission():
        nonlocal writer_ahead
        if writer_ahead:
            # An upload can obtain SQLite's writer lock before the scheduler,
            # creating a job after the scheduler has begun trying to claim.
            writer_ahead = False
            waiting_submission.append(accepted(service))
        with original_transaction() as connection:
            yield connection

    monkeypatch.setattr(service.db, "transaction", transaction_after_submission)
    claimed = service._claim()
    assert claimed is not None
    submitted = waiting_submission[0]
    assert claimed["id"] == submitted["id"]
    assert claimed["started_at"] >= submitted["created_at"]
    attempt = service.list_attempts("alice", submitted["id"])[0]
    assert attempt["started_at"] >= attempt["accepted_at"]
    assert service.get_usage("alice")["used"] == 1


@pytest.mark.parametrize("changed", ["bytes", "name", "engine", "order"])
def test_submission_payload_mismatch_is_atomic_conflict(service, changed):
    files = [upload(filename="one.txt"), upload(filename="two.txt", data=b"second")]
    service.enqueue("alice", files, submission_key="key")
    engine = "markitdown"
    if changed == "bytes":
        files[0] = upload(data=b"other", filename="one.txt")
    elif changed == "name":
        files[0] = upload(filename="renamed.txt")
    elif changed == "engine":
        engine = "docling"
    else:
        files.reverse()
    assert_error(
        409, lambda: service.enqueue("alice", files, engine, submission_key="key")
    )
    assert service.get_usage("alice")["used"] == 2
    assert len(service.list_jobs("alice")) == 2


@pytest.mark.parametrize("key", ["", "has space", "x" * 129, 12, "newline\n"])
def test_invalid_submission_keys_do_not_charge(service, key):
    assert_error(400, lambda: accepted(service, key=key))
    assert service.get_usage("alice")["used"] == 0


def test_replay_works_after_mutable_quota_decrease_but_not_account_disable(service):
    first = accepted(service)
    user_update(service, daily_quota=1, max_file_bytes=1)
    assert accepted(service)["id"] == first["id"]
    user_update(service, is_active=0)
    assert_error(403, lambda: accepted(service))


def test_deleted_submission_cannot_be_recreated_by_replay(service):
    first = accepted(service)
    service.delete_job("alice", first["id"])
    assert_error(410, lambda: accepted(service))
    assert service.get_usage("alice")["used"] == 1
    assert len(service.list_jobs("alice")) == 0
    with service.db.connect() as connection:
        tombstone = dict(connection.execute("SELECT * FROM job_tombstones").fetchone())
        assert (
            connection.execute("SELECT COUNT(*) FROM job_attempts").fetchone()[0] == 0
        )
    assert tombstone["job_id"] == first["id"]
    assert tombstone["user_id"] == "alice"
    assert tombstone["reason"] == "user_deleted"
    service.cleanup(time.time() + service.settings.history_seconds * 2)
    with service.db.connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM job_tombstones").fetchone()[0] == 1
        )
    assert accepted(service, key="new-key")["id"] != first["id"]


def test_source_and_submission_snapshot_are_immutable_across_retry(
    service, monkeypatch
):
    current = {"version": 1, "retention_seconds": 4000}
    seen_connections = []

    def provider(connection=None):
        assert connection.in_transaction
        seen_connections.append(connection)
        return dict(current)

    service.config_provider = provider
    first = accepted(service)
    snapshot = first["submission_snapshot"]
    assert snapshot["config_version"] == 1
    assert first["source_sha256"] == hashlib.sha256(b"hello").hexdigest()
    assert first["expires_at"] == first["created_at"] + 4000
    raw = {k: v for k, v in snapshot.items() if k != "snapshot_sha256"}
    assert (
        snapshot["snapshot_sha256"]
        == hashlib.sha256(service._json(raw).encode()).hexdigest()
    )
    assert "/" not in json.dumps(snapshot["deployment"])
    service.cancel("alice", first["id"])
    current.update(version=2, retention_seconds=8000)
    retried = service.retry("alice", first["id"])
    assert retried["submission_snapshot"] == snapshot
    assert retried["expires_at"] == first["expires_at"]
    assert retried["attempt_history"][0]["snapshot"] == snapshot
    assert retried["attempt_history"][1]["snapshot"]["config_version"] == 2
    assert retried["attempt_history"][1]["reason"] == "retry"
    assert retried["accepted_attempts"] == 2
    assert retried["quota_charged"] is True
    assert len(seen_connections) == 2


def test_fractional_trusted_retention_is_preserved(service):
    service.config_provider = lambda connection=None: {
        "version": 1,
        "retention_seconds": 0.05,
    }
    first = accepted(service)
    assert first["expires_at"] - first["created_at"] == pytest.approx(0.05, abs=1e-6)


def test_retry_replay_is_counted_once_and_owner_checked(service):
    first = accepted(service)
    service.cancel("alice", first["id"])

    def retry(_):
        return service.retry("alice", first["id"], submission_key="retry-one")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(retry, range(8)))
    assert len({item["current_attempt_id"] for item in results}) == 1
    assert service.get_usage("alice")["used"] == 2
    assert len(service.list_attempts("alice", first["id"])) == 2
    assert_error(
        404, lambda: service.retry("bob", first["id"], submission_key="retry-one")
    )
    assert_error(404, lambda: service.list_attempts("bob", first["id"]))


def test_cancelled_queued_admissions_count_toward_attempt_limit(service):
    first = accepted(service)
    for index in range(service.settings.max_attempts):
        cancelled = service.cancel("alice", first["id"])
        assert cancelled["lifecycle_status"] == "cancelled"
        if index < service.settings.max_attempts - 1:
            service.retry("alice", first["id"])
    assert_error(409, lambda: service.retry("alice", first["id"]))
    assert service.get_usage("alice")["used"] == service.settings.max_attempts
    assert all(
        item["state"] == "cancelled"
        for item in service.list_attempts("alice", first["id"])
    )


def test_restart_creates_counted_attempt_but_queued_is_reused(service):
    running = accepted(service)
    claimed = service._claim()
    assert claimed["id"] == running["id"]
    queued = accepted(service, key="another", filename="queued.txt")
    recovered = JobService(service.db, service.settings)
    recovered._recover()
    current = recovered.get_job("alice", running["id"])
    assert [item["state"] for item in current["attempt_history"]] == [
        "interrupted",
        "queued",
    ]
    assert current["attempt_history"][0]["error_code"] == "service_restart"
    assert current["attempt_history"][1]["reason"] == "restart_recovery"
    assert len(recovered.list_attempts("alice", queued["id"])) == 1
    assert recovered.get_usage("alice")["used"] == 3
    recovered._recover()
    assert recovered.get_usage("alice")["used"] == 3


def test_restart_quota_failure_does_not_make_phantom_attempt(service):
    first = accepted(service)
    service._claim()
    user_update(service, daily_quota=1)
    recovered = JobService(service.db, service.settings)
    recovered._recover()
    current = recovered.get_job("alice", first["id"])
    assert current["status"] == "failed"
    assert len(current["attempt_history"]) == 1
    assert current["attempt_history"][0]["state"] == "interrupted"
    assert recovered.get_usage("alice")["used"] == 1


def test_changed_engine_version_requires_explicit_retry(service, monkeypatch):
    first = accepted(service)
    monkeypatch.setattr(service, "_package_version", lambda _: "9.9.9")
    assert service._claim() is None
    current = service.get_job("alice", first["id"])
    assert current["error_code"] == "profile_changed"
    retried = service.retry("alice", first["id"])
    assert retried["attempt_history"][-1]["snapshot"]["engine_version"] == "9.9.9"
    assert service._claim()["id"] == first["id"]


def test_same_size_source_tampering_is_rejected(service):
    first = accepted(service)
    (service.jobs_dir / first["id"] / "source.txt").write_bytes(b"other")
    assert service._claim() is None
    assert service.get_job("alice", first["id"])["error_code"] == "source_unavailable"


@pytest.mark.parametrize("engine", ["markitdown", "docling"])
def test_success_persists_actual_metadata_without_quality_claim(
    service, monkeypatch, engine
):
    first = service.enqueue("alice", [upload()], engine)[0]
    details = {
        "engine": "docling",
        "version": "2.133.0",
        "page_count": 2,
        "profile": "pdf-layout-local-v3",
        "ocr": False,
        "warnings": [],
        "secret": "/host/private/key",
    }
    monkeypatch.setattr(
        "markitdown_web.jobs.run_docling_conversion",
        lambda *a, **k: ("# MD", "<h1>MD</h1>", details),
    )
    controls = []

    def convert(path, suffix, *, settings=None, cancelled=None):
        controls.append((settings, cancelled()))
        return "# MD", "<h1>MD</h1>"

    monkeypatch.setattr("markitdown_web.jobs.run_conversion", convert)
    service._execute(service._claim())
    current = service.get_job("alice", first["id"], True)
    assert current["status"] == "succeeded"
    metadata = current["metadata"]
    assert metadata["engine"] == engine
    assert metadata["quality_assessment"] == "not_evaluated"
    assert metadata["markdown_sha256"] == hashlib.sha256(b"# MD").hexdigest()
    assert metadata["source_sha256"] == first["source_sha256"]
    assert "secret" not in metadata
    if engine == "markitdown":
        assert "page_count" not in metadata
        assert controls == [(service.settings, False)]
    attempt = current["attempt_history"][0]
    assert attempt["state"] == "succeeded"
    assert attempt["metadata"] == metadata
    assert (
        attempt["physical_released_at"]
        >= attempt["finished_at"]
        >= attempt["started_at"]
    )


def test_cancel_revoke_publish_but_keep_physical_slot_and_attempt_identity(
    service, monkeypatch
):
    entered, release = threading.Event(), threading.Event()
    callbacks = []

    def convert(path, suffix, *, cancelled=None, settings=None):
        entered.set()
        assert release.wait(5)
        callbacks.append(cancelled())
        return "late secret", "<p>late secret</p>"

    monkeypatch.setattr("markitdown_web.jobs.run_conversion", convert)
    first = accepted(service)
    claimed = service._claim()
    worker = threading.Thread(target=service._execute, args=(claimed,))
    worker.start()
    try:
        assert entered.wait(3)
        cancelled = service.cancel("alice", first["id"])
        assert cancelled["lifecycle_status"] == "stopping"
        assert cancelled["attempt_history"][0]["physical_released_at"] is None
        assert_error(409, lambda: service.retry("alice", first["id"]))
        accepted(service, key="second")
        assert service._claim() is None
        release.set()
        worker.join(3)
        assert not worker.is_alive()
        final = service.get_job("alice", first["id"])
        assert final["lifecycle_status"] == "cancelled"
        assert final["attempt_history"][0]["physical_released_at"] is not None
        assert not (service.jobs_dir / first["id"] / "markdown.md").exists()
        assert callbacks == [True]
        assert service.get_usage("alice")["used"] == 2
    finally:
        release.set()
        worker.join(5)


def test_legacy_job_provenance_remains_unrecorded(service):
    first = accepted(service)
    with service.db.transaction() as connection:
        connection.execute("DELETE FROM job_attempts WHERE job_id=?", (first["id"],))
        connection.execute(
            "UPDATE jobs SET source_sha256=NULL,submission_snapshot=NULL,current_attempt_id=NULL WHERE id=?",
            (first["id"],),
        )
    legacy = JobService(service.db, service.settings).get_job("alice", first["id"])
    assert legacy["provenance_status"] == "unrecorded"
    assert legacy["submission_snapshot"] is None
    assert legacy["source_sha256"] is None
    assert legacy["attempt_history"] == []
    assert legacy["quota_charged"] is None
    assert legacy["attempt_history_complete"] is False


def test_history_cleanup_tombstones_and_replay_rejection(service):
    first = accepted(service)
    expiration = time.time() - 1
    update(service, first["id"], expires_at=expiration)
    service.cleanup(expiration + service.settings.history_seconds + 2)
    assert_error(410, lambda: accepted(service))
    with service.db.connect() as connection:
        assert (
            connection.execute(
                "SELECT reason FROM job_tombstones WHERE job_id=?", (first["id"],)
            ).fetchone()[0]
            == "history_expired"
        )


def test_failed_attempt_records_error_and_never_invents_output_hash(
    service, monkeypatch
):
    first = accepted(service)

    def fail(*a, **k):
        raise ConversionError("转换超时（45 秒）")

    monkeypatch.setattr("markitdown_web.jobs.run_conversion", fail)
    service._execute(service._claim())
    current = service.get_job("alice", first["id"])
    assert current["error_code"] == "timeout"
    assert current["quota_charged"] is True
    assert current["attempt_history"][0]["metadata"] == {}
    assert current["attempt_history"][0]["markdown_sha256"] is None


def test_cleanup_sweeps_only_stale_inactive_known_scratch(
    service, monkeypatch, tmp_path
):
    import os

    stale = service.root / "engine-old12345"
    live = service.root / "engine-live1234"
    fresh = service.root / "engine-new12345"
    unrelated = service.root / "important-old"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    link = service.root / "engine-link1234"
    link.symlink_to(outside)
    for path in (stale, live, fresh, unrelated):
        path.mkdir()
        (path / "private").write_text("private")
    old = time.time() - 7200
    for path in (stale, live, unrelated, link):
        os.utime(path, (old, old), follow_symlinks=False)
    monkeypatch.setattr("markitdown_web.sandbox.active_workspaces", lambda: {live})
    service.cleanup()
    assert not stale.exists()
    assert live.exists()
    assert fresh.exists()
    assert unrelated.exists()
    assert not link.exists()
    assert (outside / "keep.txt").read_text() == "keep"
    status = service.cleanup_status()
    assert status["last_succeeded_at"] is not None
    assert status["consecutive_failures"] == 0
    assert status["last_counts"]["orphan_scratch_dirs_removed"] == 2
    assert "private" not in json.dumps(status)


def test_cleanup_failure_is_visible_and_success_clears_error(service, monkeypatch):
    real = service._cleanup

    def fail(_):
        raise OSError("/host/secret detail")

    monkeypatch.setattr(service, "_cleanup", fail)
    with pytest.raises(OSError):
        service.cleanup()
    status = service.cleanup_status()
    assert status["last_error_code"] == "cleanup_failed"
    assert status["consecutive_failures"] == 1
    assert "secret" not in json.dumps(status)
    monkeypatch.setattr(service, "_cleanup", real)
    service.cleanup()
    status = service.cleanup_status()
    assert status["consecutive_failures"] == 0
    assert status["last_error_code"] is None
    assert status["last_failed_at"] is not None


def test_concurrent_identical_requests_run_preflight_once(service, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def preflight(*args):
        calls.append(args)
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr("markitdown_web.jobs.validate_engine_uploads", preflight)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(accepted, service)
        assert entered.wait(3)
        second = pool.submit(accepted, JobService(service.db, service.settings))
        try:
            assert not second.done()
        finally:
            release.set()
        assert first.result()["id"] == second.result()["id"]
    assert len(calls) == 1
    from markitdown_web.jobs import _SUBMISSIONS_IN_PROGRESS

    assert _SUBMISSIONS_IN_PROGRESS == {}


def test_rejected_staging_does_not_persist_idempotency_or_attempt(service, monkeypatch):
    original = service._write

    def fail(*args):
        raise OSError("synthetic")

    monkeypatch.setattr(service, "_write", fail)
    with pytest.raises(OSError):
        accepted(service)
    with service.db.connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM job_submissions").fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM job_attempts").fetchone()[0] == 0
        )
    assert service.get_usage("alice")["used"] == 0
    monkeypatch.setattr(service, "_write", original)
    assert accepted(service)["status"] == "queued"


def test_replay_of_expired_job_has_expired_state_without_charge(service):
    first = accepted(service)
    update(service, first["id"], expires_at=time.time() - 1)
    replayed = accepted(service)
    assert replayed["status"] == "expired"
    assert replayed["attempt_history"][0]["state"] == "expired"
    assert service.get_usage("alice")["used"] == 1


def test_worker_reported_version_is_distinct_from_supervisor(service, monkeypatch):
    first = accepted(service)

    def convert(path, suffix, *, cancelled=None, settings=None, runtime_metadata=None):
        runtime_metadata.update(
            version="0.1.42", python="3.12.42", path="/private/path"
        )
        return "actual worker output", "<p>actual worker output</p>"

    monkeypatch.setattr("markitdown_web.jobs.run_conversion", convert)
    service._execute(service._claim())
    result = service.get_job("alice", first["id"])
    assert result["metadata"]["version"] == "0.1.42"
    assert result["metadata"]["python_version"] == "3.12.42"
    assert result["metadata"]["version_source"] == "worker_reported"
    assert "path" not in result["metadata"]
    assert "supervisor_versions" in result["submission_snapshot"]


def test_usage_reports_current_configuration_not_old_task_snapshot(service):
    current = {"version": 1, "retention_seconds": 3600}
    service.config_provider = lambda connection=None: dict(current)
    first = accepted(service)
    current.update(version=2, retention_seconds=7200)
    usage = service.get_usage("alice")
    assert usage["retention_seconds"] == 7200
    assert usage["config_version"] == 2
    assert (
        service.get_job("alice", first["id"])["submission_snapshot"]["config_version"]
        == 1
    )


def test_shutdown_rejects_new_admission_and_retry_but_allows_read_only_replay(service):
    first = accepted(service)
    service.cancel("alice", first["id"])
    service._stop.set()
    assert accepted(service)["id"] == first["id"]
    assert_error(503, lambda: accepted(service, key="new"))
    assert_error(503, lambda: service.retry("alice", first["id"]))
    assert service.get_usage("alice")["used"] == 1


def test_shutdown_during_preflight_does_not_charge(service, monkeypatch):
    monkeypatch.setattr(
        "markitdown_web.jobs.validate_engine_uploads", lambda *args: service._stop.set()
    )
    assert_error(503, lambda: accepted(service))
    assert service.get_usage("alice")["used"] == 0
    assert service.list_jobs("alice") == []


def test_failed_worker_records_runtime_provenance_without_output_claims(
    service, monkeypatch
):
    first = accepted(service)

    def fail(path, suffix, *, runtime_metadata=None, **kwargs):
        runtime_metadata.update(version="0.1.42", python="3.12.42")
        raise ConversionError("无法转换此文件")

    monkeypatch.setattr("markitdown_web.jobs.run_conversion", fail)
    service._execute(service._claim())
    current = service.get_job("alice", first["id"])
    attempt = current["attempt_history"][0]
    assert current["status"] == "failed"
    assert current["metadata"] == {}
    assert attempt["metadata"]["version"] == "0.1.42"
    assert attempt["metadata"]["version_source"] == "worker_reported"
    assert attempt["markdown_sha256"] is None
    assert "quality_assessment" not in attempt["metadata"]


def test_worker_logs_only_safe_error_codes_and_keeps_diagnostics(
    service, monkeypatch, caplog
):
    def fail():
        service._stop.set()
        service._wake.set()
        raise RuntimeError("/private/document.txt secret document content")

    monkeypatch.setattr(service, "_claim", fail)
    service._worker()
    assert "worker_iteration_failed" in caplog.text
    assert "/private" not in caplog.text
    assert "secret" not in caplog.text
    assert "Traceback" not in caplog.text
    diagnostics = service.cleanup_status()
    assert diagnostics["worker_failures"] == 1
    assert diagnostics["last_worker_error_code"] == "worker_iteration_failed"
