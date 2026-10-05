"""Synthetic, offline tests for persistent per-account conversion jobs."""

from __future__ import annotations

import io
import os
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from markitdown_web.conversion import MAX_MARKDOWN_BYTES, ConversionError
from markitdown_web.jobs import OUTPUT_RESERVATION, JobError, JobService
from markitdown_web.state import Database, Settings


@pytest.fixture
def service(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "private", start_workers=False, cleanup_interval=0.1
    )
    db = Database(settings)
    with db.connect() as connection:
        connection.execute(
            """CREATE TABLE users (
            id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0, is_active INTEGER NOT NULL DEFAULT 1,
            daily_quota INTEGER NOT NULL DEFAULT 100, max_file_bytes INTEGER NOT NULL DEFAULT 20971520,
            created_at REAL NOT NULL DEFAULT 0
        )"""
        )
        connection.executemany(
            "INSERT INTO users(id,username,is_admin) VALUES (?,?,?)",
            [("alice", "alice", 0), ("bob", "bob", 1)],
        )
    result = JobService(db, settings)
    yield result
    result.stop()


def upload(data=b"hello", filename="document.txt"):
    return {
        "filename": filename,
        "suffix": Path(filename.replace("\\", "/")).suffix.lower(),
        "data": data,
    }


def job(service, user="alice", **kwargs):
    return service.enqueue(user, [upload(**kwargs)])[0]


def update(service, job_id, **values):
    with service.db.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET "
            + ",".join(f"{name}=?" for name in values)
            + " WHERE id=?",
            [*values.values(), job_id],
        )


def internal_job(service, job_id):
    with service.db.connect() as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row is not None else None


def user_update(service, user="alice", **values):
    with service.db.transaction() as connection:
        connection.execute(
            "UPDATE users SET "
            + ",".join(f"{name}=?" for name in values)
            + " WHERE id=?",
            [*values.values(), user],
        )


def finish(service, job_id, text="# Hello"):
    path = service.jobs_dir / job_id
    service._write(path / "markdown.md", text.encode())
    service._write(path / "preview.html", b"<h1>Hello</h1>")
    update(service, job_id, status="succeeded", attempts=1, finished_at=time.time())


def wait_for(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    pytest.fail("Condition did not become true before timeout")


def test_enqueue_persists_private_source_and_metadata(service):
    result = job(service, filename="../../项目\\secret\n.txt")
    assert result["filename"] == "secret.txt"
    assert result["status"] == "queued"
    assert result["attempts"] == 0
    assert isinstance(result["created_at"], float)
    assert len(result["id"]) == 32
    path = service.jobs_dir / result["id"]
    assert (path / "source.txt").read_bytes() == b"hello"
    assert os.stat(path).st_mode & 0o777 == 0o700
    assert os.stat(path / "source.txt").st_mode & 0o777 == 0o600
    another = JobService(service.db, service.settings)
    assert another.get_job("alice", result["id"]) == result
    assert service.get_usage("alice")["used"] == 1
    assert service.get_usage("bob")["used"] == 0


@pytest.mark.parametrize(
    "files,code",
    [
        ([], 400),
        ([upload(filename="a.exe")], 400),
        ([upload(data=b"")], 413),
        ([{"filename": "a.txt", "suffix": ".pdf", "data": b"x"}], 400),
        ([upload()] * 11, 400),
    ],
)
def test_validate_batch(service, files, code):
    with pytest.raises(JobError) as error:
        service.enqueue("alice", files)
    assert error.value.status_code == code
    assert service.list_jobs("alice") == []
    assert list(service.jobs_dir.iterdir()) == []


def test_atomic_batch_quota_storage_and_queue_failures(service):
    user_update(service, daily_quota=1)
    with pytest.raises(JobError) as error:
        service.enqueue("alice", [upload(), upload()])
    assert error.value.status_code == 429
    assert service.get_usage("alice")["used"] == 0
    assert list(service.jobs_dir.iterdir()) == []
    tiny = JobService(
        service.db, replace(service.settings, max_storage_bytes=OUTPUT_RESERVATION)
    )
    with pytest.raises(JobError) as error:
        job(tiny)
    assert error.value.status_code == 507
    assert tiny.get_usage("alice")["used"] == 0
    full = JobService(service.db, replace(service.settings, max_queue_jobs=0))
    with pytest.raises(JobError) as error:
        job(full)
    assert error.value.status_code == 429
    assert list(service.jobs_dir.iterdir()) == []


def test_account_and_global_file_limits_are_revalidated(service):
    user_update(service, max_file_bytes=2)
    with pytest.raises(JobError) as error:
        job(service)
    assert error.value.status_code == 413
    assert list(service.jobs_dir.iterdir()) == []
    smaller = JobService(service.db, replace(service.settings, max_total_bytes=3))
    with pytest.raises(JobError) as error:
        smaller.enqueue("bob", [upload(data=b"xx"), upload(data=b"xx")])
    assert error.value.status_code == 413
    user_update(service, is_active=0)
    with pytest.raises(JobError) as error:
        job(service)
    assert error.value.status_code == 403


def test_quota_race_is_atomic_across_service_instances(service):
    user_update(service, daily_quota=3)
    second = JobService(service.db, service.settings)

    def submit(index):
        try:
            return job(service if index % 2 else second)["id"]
        except JobError as exc:
            assert exc.status_code == 429
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(submit, range(12)))
    assert len([value for value in results if value]) == 3
    assert service.get_usage("alice")["used"] == 3
    assert len(service.list_jobs("alice")) == 3
    assert len(list(service.jobs_dir.iterdir())) == 3


def test_ownership_admin_has_no_document_override(service):
    result = job(service)
    finish(service, result["id"])
    for action in [
        lambda: service.get_job("bob", result["id"], True),
        lambda: service.download("bob", result["id"]),
        lambda: service.retry("bob", result["id"]),
        lambda: service.delete_job("bob", result["id"]),
        lambda: service.archive("bob", [result["id"]]),
    ]:
        with pytest.raises(JobError) as error:
            action()
        assert error.value.status_code == 404
    assert service.list_jobs("bob") == []
    assert service.get_job("alice", result["id"], True)["markdown"] == "# Hello"
    with pytest.raises(JobError) as error:
        service.get_job("alice", "../../outside")
    assert error.value.status_code == 404


def test_zip_validates_every_owner_and_uses_safe_unique_names(service):
    first = job(service, filename="../../same.txt")
    second = job(service, filename="same.md")
    other = job(service, "bob")
    for result in (first, second, other):
        finish(service, result["id"])
    output = service.archive("alice", [first["id"], second["id"]])
    with zipfile.ZipFile(io.BytesIO(output)) as archive:
        assert len(set(archive.namelist())) == 2
        assert all(
            "/" not in name and "\\" not in name and name.endswith(".md")
            for name in archive.namelist()
        )
        assert all(archive.read(name) == b"# Hello" for name in archive.namelist())
    for ids, status in [
        ([first["id"], other["id"]], 404),
        ([first["id"], first["id"]], 400),
        ([], 400),
        ([uuid.uuid4().hex] * 11, 400),
    ]:
        with pytest.raises(JobError) as error:
            service.archive("alice", ids)
        assert error.value.status_code == status


def test_expiry_denies_content_then_cleanup_removes_history(service):
    result = job(service)
    finish(service, result["id"])
    expiration = time.time() - 1
    update(service, result["id"], expires_at=expiration)
    for action in [
        lambda: service.get_job("alice", result["id"], True),
        lambda: service.download("alice", result["id"]),
        lambda: service.archive("alice", [result["id"]]),
        lambda: service.retry("alice", result["id"]),
    ]:
        with pytest.raises(JobError) as error:
            action()
        assert error.value.status_code == 410
    assert service.get_job("alice", result["id"])["status"] == "expired"
    service.cleanup()
    assert not (service.jobs_dir / result["id"]).exists()
    service.cleanup(expiration + service.settings.history_seconds + 1)
    with pytest.raises(JobError) as error:
        service.get_job("alice", result["id"])
    assert error.value.status_code == 404


def test_expired_capacity_is_reclaimed_before_new_upload(service):
    bounded = JobService(
        service.db, replace(service.settings, max_storage_bytes=OUTPUT_RESERVATION + 5)
    )
    first = job(bounded)
    update(bounded, first["id"], expires_at=time.time() - 1)
    second = job(bounded)
    assert second["status"] == "queued"
    assert not (bounded.jobs_dir / first["id"]).exists()


def test_retry_charges_quota_and_enforces_attempt_limit(service):
    result = job(service)
    update(service, result["id"], status="failed", attempts=1)
    retried = service.retry("alice", result["id"])
    assert retried["status"] == "queued"
    assert service.get_usage("alice")["used"] == 2
    update(service, result["id"], status="failed", attempts=3)
    with pytest.raises(JobError) as error:
        service.retry("alice", result["id"])
    assert error.value.status_code == 409
    assert service.get_usage("alice")["used"] == 2


def test_retry_quota_rollback_and_missing_source(service):
    result = job(service)
    update(service, result["id"], status="failed", attempts=1)
    user_update(service, daily_quota=1)
    with pytest.raises(JobError) as error:
        service.retry("alice", result["id"])
    assert error.value.status_code == 429
    assert service.get_job("alice", result["id"])["status"] == "failed"
    (service.jobs_dir / result["id"] / "source.txt").unlink()
    with pytest.raises(JobError) as error:
        service.retry("alice", result["id"])
    assert error.value.status_code == 410


def test_restart_recovers_interrupted_jobs_and_lock_is_exclusive(service, monkeypatch):
    retryable = job(service)
    exhausted = job(service)
    expired = job(service)
    update(service, retryable["id"], status="running", attempts=1)
    update(service, exhausted["id"], status="running", attempts=3)
    update(
        service, expired["id"], status="running", attempts=1, expires_at=time.time() - 1
    )
    monkeypatch.setattr(service, "_worker", lambda: None)
    service.start()
    assert service.get_job("alice", retryable["id"])["status"] == "queued"
    assert service.get_job("alice", exhausted["id"])["status"] == "failed"
    assert service.get_job("alice", expired["id"])["status"] == "expired"
    assert service.get_usage("alice")["used"] == 4
    second = JobService(service.db, service.settings)
    with pytest.raises(RuntimeError, match="Another worker"):
        second.start()
    service.stop()
    monkeypatch.setattr(second, "_worker", lambda: None)
    second.start()
    second.stop()


def test_disabled_queued_jobs_never_start(service, monkeypatch):
    result = job(service)
    user_update(service, is_active=0)
    calls = []
    monkeypatch.setattr(
        "markitdown_web.jobs.run_conversion", lambda *args: calls.append(args)
    )
    service.start()
    wait_for(lambda: internal_job(service, result["id"])["status"] == "failed")
    assert calls == []
    assert internal_job(service, result["id"])["attempts"] == 0
    with pytest.raises(JobError) as error:
        service.get_job("alice", result["id"])
    assert error.value.status_code == 403


def test_workers_enforce_global_and_per_user_concurrency(service, monkeypatch):
    release = threading.Event()
    lock = threading.Lock()
    active = {"alice": 0, "bob": 0}
    peaks = {"total": 0, "alice": 0, "bob": 0}
    started = []

    def convert(path, suffix):
        user = path.read_text()
        with lock:
            active[user] += 1
            started.append(user)
            peaks["total"] = max(peaks["total"], sum(active.values()))
            peaks[user] = max(peaks[user], active[user])
        assert release.wait(4)
        with lock:
            active[user] -= 1
        return user, f"<p>{user}</p>"

    monkeypatch.setattr("markitdown_web.jobs.run_conversion", convert)
    for user in ("alice", "bob"):
        service.enqueue(user, [upload(data=user.encode())] * 3)
    service.start()
    try:
        wait_for(lambda: len(started) == 2)
        assert sorted(started) == ["alice", "bob"]
        assert service.get_usage("alice")["active_jobs"] == 3
        release.set()
        wait_for(
            lambda: all(
                item["status"] == "succeeded"
                for user in ("alice", "bob")
                for item in service.list_jobs(user)
            )
        )
    finally:
        release.set()
    assert peaks == {"total": 2, "alice": 1, "bob": 1}
    assert all(item["attempts"] == 1 for item in service.list_jobs("alice"))


@pytest.mark.parametrize("change", ["delete", "disable", "expire"])
def test_running_job_cannot_restore_deleted_expired_or_disabled_result(
    service, monkeypatch, change
):
    entered = threading.Event()
    release = threading.Event()

    def convert(path, suffix):
        entered.set()
        assert release.wait(4)
        return "private content", "<p>private content</p>"

    monkeypatch.setattr("markitdown_web.jobs.run_conversion", convert)
    result = job(service)
    service.start()
    try:
        assert entered.wait(3)
        if change == "delete":
            service.delete_job("alice", result["id"])
        elif change == "disable":
            user_update(service, is_active=0)
        else:
            update(service, result["id"], expires_at=time.time() - 1)
        release.set()
        service.stop()
    finally:
        release.set()
    if change == "delete":
        with pytest.raises(JobError) as error:
            service.get_job("alice", result["id"])
        assert error.value.status_code == 404
        assert service.list_jobs("alice") == []
    elif change == "disable":
        assert internal_job(service, result["id"])["status"] == "failed"
        with pytest.raises(JobError) as error:
            service.get_job("alice", result["id"])
        assert error.value.status_code == 403
    else:
        assert service.get_job("alice", result["id"])["status"] == "expired"
    assert not (service.jobs_dir / result["id"] / "markdown.md").exists()
    with pytest.raises(JobError):
        service.download("alice", result["id"])


def test_converter_errors_are_safe_and_output_limits_apply(service, monkeypatch):
    for failure in (
        ValueError("/private/path and secret contents"),
        ConversionError("转换超时（45 秒）"),
    ):
        result = job(service)

        def fail(*args):
            raise failure

        monkeypatch.setattr("markitdown_web.jobs.run_conversion", fail)
        claimed = service._claim()
        service._execute(claimed)
        current = service.get_job("alice", result["id"])
        assert current["status"] == "failed"
        assert "secret" not in current["error"]
        assert "/private" not in current["error"]
    result = job(service)
    monkeypatch.setattr(
        "markitdown_web.jobs.run_conversion",
        lambda *args: ("x" * (MAX_MARKDOWN_BYTES + 1), ""),
    )
    service._execute(service._claim())
    assert service.get_job("alice", result["id"])["status"] == "failed"


def test_source_and_output_symlinks_are_not_followed(service, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("hello")
    result = job(service)
    source = service.jobs_dir / result["id"] / "source.txt"
    source.unlink()
    source.symlink_to(outside)
    assert service._claim() is None
    assert service.get_job("alice", result["id"])["status"] == "failed"
    finish(service, result["id"])
    output = service.jobs_dir / result["id"] / "markdown.md"
    output.unlink()
    output.symlink_to(outside)
    with pytest.raises(JobError) as error:
        service.download("alice", result["id"])
    assert error.value.status_code == 410
    service.delete_job("alice", result["id"])
    assert outside.read_text() == "hello"


def test_storage_root_and_lock_symlinks_are_rejected(service, tmp_path):
    link = tmp_path / "linked"
    link.symlink_to(service.root, target_is_directory=True)
    with pytest.raises((RuntimeError, ValueError), match="symbolic links|symlink"):
        JobService(service.db, replace(service.settings, data_dir=link))
    outside = tmp_path / "outside.lock"
    outside.write_text("do not modify")
    (service.root / "worker.lock").symlink_to(outside)
    with pytest.raises(RuntimeError, match="symbolic link"):
        service.start()
    assert outside.read_text() == "do not modify"


def test_cleanup_prunes_old_usage_and_orphan_staging(service):
    orphan = service.jobs_dir / uuid.uuid4().hex
    orphan.mkdir(mode=0o700)
    (orphan / "source.txt").write_bytes(b"orphan")
    os.utime(orphan, (time.time() - 7200,) * 2)
    fresh = service.jobs_dir / uuid.uuid4().hex
    fresh.mkdir(mode=0o700)
    with service.db.connect() as connection:
        connection.execute(
            "INSERT INTO daily_usage(user_id,day,used) VALUES ('alice','2000-01-01',7)"
        )
    service.cleanup()
    assert not orphan.exists()
    assert fresh.exists()
    with service.db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM daily_usage").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["delete", "expire"])
def test_physical_slot_remains_reserved_after_running_job_disappears(
    service, monkeypatch, change
):
    entered = threading.Event()
    release = threading.Event()
    second_started = threading.Event()

    def convert(path, suffix):
        label = path.read_bytes()
        if label == b"first":
            entered.set()
            assert release.wait(4)
        else:
            second_started.set()
        return "ok", "<p>ok</p>"

    monkeypatch.setattr("markitdown_web.jobs.run_conversion", convert)
    first = job(service, data=b"first")
    service.start()
    try:
        assert entered.wait(3)
        if change == "delete":
            service.delete_job("alice", first["id"])
        else:
            update(service, first["id"], expires_at=time.time() - 1)
            service.cleanup()
        second = job(service, data=b"second")
        # An idle global worker exists, but this user's physical slot is occupied.
        assert not second_started.wait(0.15)
        assert service.get_job("alice", second["id"])["status"] == "queued"
        release.set()
        assert second_started.wait(3)
        wait_for(
            lambda: service.get_job("alice", second["id"])["status"] == "succeeded"
        )
    finally:
        release.set()


def test_rejected_admission_does_not_stage_and_failed_staging_rolls_back(
    service, monkeypatch
):
    user_update(service, daily_quota=0)
    writes = []
    original = service._write

    def write(path, data):
        writes.append(path)
        original(path, data)

    monkeypatch.setattr(service, "_write", write)
    with pytest.raises(JobError) as error:
        job(service)
    assert error.value.status_code == 429
    assert writes == []
    user_update(service, daily_quota=10)

    def fail(path, data):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(service, "_write", fail)
    with pytest.raises(OSError, match="synthetic"):
        job(service)
    assert service.get_usage("alice")["used"] == 0
    assert service.list_jobs("alice") == []
    assert list(service.jobs_dir.iterdir()) == []


def test_retry_race_only_charges_once(service):
    result = job(service)
    update(service, result["id"], status="failed", attempts=1)

    def retry(_):
        try:
            return service.retry("alice", result["id"])
        except JobError as exc:
            assert exc.status_code == 409
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(retry, range(8)))
    assert sum(value is not None for value in results) == 1
    assert service.get_usage("alice")["used"] == 2


def test_succeeded_and_failed_jobs_reserve_storage_until_deleted(service):
    bounded = JobService(
        service.db, replace(service.settings, max_storage_bytes=OUTPUT_RESERVATION + 5)
    )
    result = job(bounded)
    finish(bounded, result["id"])
    with pytest.raises(JobError) as error:
        job(bounded)
    assert error.value.status_code == 507
    bounded.delete_job("alice", result["id"])
    assert job(bounded)["status"] == "queued"


def test_result_file_is_private_during_conversion_and_removed_after(
    service, monkeypatch
):
    result = job(service)

    def convert(path, suffix):
        temporary = path.parent / "result.json"
        assert temporary.exists()
        assert temporary.stat().st_mode & 0o777 == 0o600
        temporary.write_text('{"markdown":"converted"}')
        return "converted", "<p>converted</p>"

    monkeypatch.setattr("markitdown_web.jobs.run_conversion", convert)
    service._execute(service._claim())
    directory = service.jobs_dir / result["id"]
    assert not (directory / "result.json").exists()
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in directory.iterdir())
    assert service.download("alice", result["id"])[1] == b"converted"


def test_real_bounded_conversion_persists_markdown_and_preview(service):
    result = job(service, data=b"Persistent conversion smoke test")
    service._execute(service._claim())
    completed = service.get_job("alice", result["id"], include_content=True)
    assert completed["status"] == "succeeded", completed["error"]
    assert "Persistent conversion smoke test" in completed["markdown"]
    assert "Persistent conversion smoke test" in completed["html"]
    assert completed["size_bytes"] == completed["size"]
    assert service.download("alice", result["id"])[1] == completed["markdown"].encode()


def test_explicit_delete_removes_history_without_refunding_quota(service):
    result = job(service)
    finish(service, result["id"])
    assert service.delete_job("alice", result["id"]) == {
        "id": result["id"],
        "deleted": True,
    }
    assert service.list_jobs("alice") == []
    assert not (service.jobs_dir / result["id"]).exists()
    assert service.get_usage("alice")["used"] == 1
    for action in [
        lambda: service.get_job("alice", result["id"]),
        lambda: service.delete_job("alice", result["id"]),
        lambda: service.download("alice", result["id"]),
        lambda: service.retry("alice", result["id"]),
    ]:
        with pytest.raises(JobError) as error:
            action()
        assert error.value.status_code == 404
    with service.db.connect() as connection:
        assert (
            connection.execute(
                "SELECT id FROM jobs WHERE id=?", (result["id"],)
            ).fetchone()
            is None
        )


def test_disabled_account_cannot_read_download_archive_or_delete(service):
    result = job(service)
    finish(service, result["id"])
    user_update(service, is_active=0)
    for action in [
        lambda: service.get_job("alice", result["id"]),
        lambda: service.get_job("alice", result["id"], include_content=True),
        lambda: service.list_jobs("alice"),
        lambda: service.download("alice", result["id"]),
        lambda: service.archive("alice", [result["id"]]),
        lambda: service.delete_job("alice", result["id"]),
        lambda: service.retry("alice", result["id"]),
        lambda: service.get_usage("alice"),
    ]:
        with pytest.raises(JobError) as error:
            action()
        assert error.value.status_code == 403
    # No disabled action mutates the completed job; reactivation restores access.
    assert internal_job(service, result["id"])["status"] == "succeeded"
    user_update(service, is_active=1)
    assert service.download("alice", result["id"])[1] == b"# Hello"


def test_terminal_write_failure_does_not_permanently_wedge_queue(service, monkeypatch):
    first = job(service)
    claimed = service._claim()
    assert claimed["id"] == first["id"]

    def failed_finalization(row):
        raise RuntimeError("synthetic transient finalization failure")

    monkeypatch.setattr(service, "_execute_claimed", failed_finalization)
    with pytest.raises(RuntimeError):
        service._execute(claimed)
    assert service.get_job("alice", first["id"])["status"] == "running"
    second = job(service)
    claimed_next = service._claim()
    assert claimed_next["id"] == second["id"]
    assert service.get_job("alice", first["id"])["status"] == "failed"
