"""Offline engine admission, persistence, cancellation and authorization coverage."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import replace

import pytest
from conftest import headers_for
from test_job_service import (
    internal_job,
    update,
    upload,
    user_update,
    wait_for,
)
from test_job_service import (
    service as service,
)

from markitdown_web.conversion import ConversionError
from markitdown_web.jobs import JobError, JobService
from markitdown_web.state import Settings


@pytest.fixture
def available(monkeypatch):
    monkeypatch.setattr(
        "markitdown_web.jobs.ensure_engine_available", lambda *args: None
    )
    monkeypatch.setattr(
        "markitdown_web.jobs.validate_engine_uploads", lambda *args: None
    )


def docling_job(service, user="alice", data=b"synthetic pdf"):
    return service.enqueue(user, [upload(data=data, filename="sample.pdf")], "docling")[
        0
    ]


def test_settings_docling_is_explicitly_opt_in(tmp_path, monkeypatch):
    for name in (
        "MARKITDOWN_DOCLING_ENABLED",
        "MARKITDOWN_DOCLING_PYTHON",
        "MARKITDOWN_DOCLING_MODELS",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(data_dir=tmp_path)
    assert settings.docling_enabled is False
    assert settings.docling_python is None
    assert settings.docling_models is None
    monkeypatch.setenv("MARKITDOWN_DOCLING_ENABLED", "1")
    monkeypatch.setenv("MARKITDOWN_DOCLING_PYTHON", "/private/runtime/python")
    monkeypatch.setenv("MARKITDOWN_DOCLING_MODELS", "/private/runtime/models")
    settings = Settings(data_dir=tmp_path)
    assert settings.docling_enabled is True
    assert str(settings.docling_python) == "/private/runtime/python"
    assert str(settings.docling_models) == "/private/runtime/models"


def test_additive_legacy_migration_preserves_jobs_owners_and_usage(service):
    result = service.enqueue("alice", [upload()])[0]
    with service.db.transaction() as connection:
        connection.execute("ALTER TABLE jobs DROP COLUMN engine")
        connection.execute("ALTER TABLE jobs DROP COLUMN metadata")
        before = dict(connection.execute("SELECT * FROM jobs").fetchone())
        owners = [dict(row) for row in connection.execute("SELECT * FROM users")]
        usage = [dict(row) for row in connection.execute("SELECT * FROM daily_usage")]
    migrated = JobService(service.db, service.settings)
    row = internal_job(migrated, result["id"])
    assert {key: row[key] for key in before} == before
    assert row["engine"] == "markitdown"
    assert row["metadata"] == "{}"
    assert migrated.get_job("alice", result["id"])["metadata"] == {}
    assert (migrated.jobs_dir / result["id"] / "source.txt").read_bytes() == b"hello"
    with service.db.connect() as connection:
        assert [
            dict(row) for row in connection.execute("SELECT * FROM users")
        ] == owners
        assert [
            dict(row) for row in connection.execute("SELECT * FROM daily_usage")
        ] == usage
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE jobs SET status='canceled'")
    # Re-running migration is idempotent.
    assert JobService(service.db, service.settings).get_job(
        "alice", result["id"]
    ) == migrated.get_job("alice", result["id"])


@pytest.mark.parametrize("engine", ["unknown", "Docling", "", None, ["docling"]])
def test_unknown_engine_rejected_before_admission(service, engine):
    with pytest.raises(JobError) as exc:
        service.enqueue("alice", [upload()], engine)
    assert exc.value.status_code == 400
    assert service.get_usage("alice")["used"] == 0
    assert service.list_jobs("alice") == []


def test_unavailable_runtime_precedes_quota_and_queue_checks(service, monkeypatch):
    user_update(service, daily_quota=0)
    service.settings = replace(service.settings, max_queue_jobs=0)

    def unavailable(*args):
        raise ConversionError("Docling 运行环境不可用")

    monkeypatch.setattr("markitdown_web.jobs.ensure_engine_available", unavailable)
    with pytest.raises(JobError, match="运行环境不可用") as exc:
        docling_job(service)
    assert exc.value.status_code == 400
    assert service.get_usage("alice")["used"] == 0
    assert service.list_jobs("alice") == []
    assert list(service.jobs_dir.iterdir()) == []


def test_preflight_is_outside_write_transaction_and_auth_is_rechecked(
    service, available, monkeypatch
):
    calls = []

    def preflight(engine, files, settings):
        assert engine == "docling"
        assert files[0]["suffix"] == ".pdf"
        with service.db.connect() as connection:
            connection.execute("PRAGMA busy_timeout=10")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE users SET is_active=0 WHERE id='alice'")
        calls.append(engine)

    monkeypatch.setattr("markitdown_web.jobs.validate_engine_uploads", preflight)
    with pytest.raises(JobError) as exc:
        docling_job(service)
    assert exc.value.status_code == 403
    assert calls == ["docling"]
    assert list(service.jobs_dir.iterdir()) == []
    # An already disabled account does not get to launch external preflight.
    with pytest.raises(JobError):
        docling_job(service)
    assert calls == ["docling"]


def test_preflight_rejection_is_atomic_for_entire_batch(
    service, available, monkeypatch
):
    def invalid(*args):
        raise ConversionError("Docling 最多支持 2 页 PDF")

    monkeypatch.setattr("markitdown_web.jobs.validate_engine_uploads", invalid)
    with pytest.raises(JobError, match="2 页"):
        service.enqueue(
            "alice", [upload(filename="a.pdf"), upload(filename="b.pdf")], "docling"
        )
    assert service.get_usage("alice")["used"] == 0
    assert service.list_jobs("alice") == []
    assert list(service.jobs_dir.iterdir()) == []


def test_engine_selection_and_safe_metadata_survive_reload(
    service, available, monkeypatch
):
    result = docling_job(service)
    metadata = {
        "engine": "docling",
        "version": "2.133.0",
        "page_count": 2,
        "duration_seconds": 1.234567,
        "profile": "pdf-layout-local-v3",
        "ocr": False,
        "warnings": [],
        "path": "/private/document.pdf",
        "source": "private contents",
    }
    monkeypatch.setattr(
        "markitdown_web.jobs.run_docling_conversion",
        lambda *args, **kwargs: ("# Result", "<h1>Result</h1>", metadata),
    )
    monkeypatch.setattr(
        "markitdown_web.jobs.run_conversion", lambda *args: pytest.fail("Wrong engine")
    )
    service._execute(service._claim())
    current = JobService(service.db, service.settings).get_job(
        "alice", result["id"], True
    )
    assert current["status"] == "succeeded"
    assert current["engine"] == "docling"
    assert current["markdown"] == "# Result"
    expected_metadata = {
        key: value
        for key, value in {**metadata, "duration_seconds": 1.235}.items()
        if key not in {"path", "source"}
    }
    assert {
        key: current["metadata"][key] for key in expected_metadata
    } == expected_metadata
    assert current["metadata"]["quality_assessment"] == "not_evaluated"
    assert current["metadata"]["source_sha256"] == result["source_sha256"]
    assert current["metadata"]["markdown_bytes"] == len(b"# Result")
    assert len(current["metadata"]["markdown_sha256"]) == 64
    assert len(current["metadata"]["html_sha256"]) == 64
    assert "private" not in internal_job(service, result["id"])["metadata"]
    update(
        service,
        result["id"],
        metadata=json.dumps(
            {
                "version": "/private/path",
                "engine": {},
                "page_count": True,
                "duration_seconds": float("nan"),
                "warnings": ["private contents"],
                "profile": "other",
                "ocr": True,
            }
        ),
    )
    assert service.get_job("alice", result["id"])["metadata"] == {}


def test_default_engine_contract_remains_two_arguments(service, available, monkeypatch):
    calls = []

    def convert(path, suffix):
        calls.append((path, suffix))
        return "default", "<p>default</p>"

    monkeypatch.setattr("markitdown_web.jobs.run_conversion", convert)
    monkeypatch.setattr(
        "markitdown_web.jobs.run_docling_conversion",
        lambda *args, **kwargs: pytest.fail("Wrong engine"),
    )
    job = service.enqueue("alice", [upload()])[0]
    service._execute(service._claim())
    assert len(calls) == 1 and calls[0][1] == ".txt"
    assert service.get_job("alice", job["id"])["engine"] == "markitdown"


def test_retry_preserves_engine_and_runtime_failure_does_not_charge(
    service, available, monkeypatch
):
    result = docling_job(service)
    update(
        service, result["id"], status="failed", attempts=1, metadata='{"page_count":2}'
    )

    def unavailable(*args):
        raise ConversionError("Docling 运行环境不可用")

    monkeypatch.setattr("markitdown_web.jobs.ensure_engine_available", unavailable)
    with pytest.raises(JobError, match="运行环境不可用"):
        service.retry("alice", result["id"])
    assert service.get_usage("alice")["used"] == 1
    assert service.get_job("alice", result["id"])["status"] == "failed"
    monkeypatch.setattr(
        "markitdown_web.jobs.ensure_engine_available", lambda *args: None
    )
    current = service.retry("alice", result["id"])
    assert current["engine"] == "docling"
    assert current["status"] == "queued"
    assert current["metadata"] == {}
    assert service.get_usage("alice")["used"] == 2


@pytest.mark.parametrize("ready", [True, False])
def test_recovery_preserves_engine_and_gates_before_charge(
    service, available, monkeypatch, ready
):
    running = docling_job(service)
    queued = docling_job(service)
    expired = docling_job(service)
    update(service, running["id"], status="running", attempts=1)
    update(
        service, expired["id"], status="running", attempts=1, expires_at=time.time() - 1
    )
    if not ready:

        def unavailable(*args):
            raise ConversionError("Docling 运行环境不可用")

        monkeypatch.setattr("markitdown_web.jobs.ensure_engine_available", unavailable)
    restarted = JobService(service.db, service.settings)
    restarted._recover()
    expected = "queued" if ready else "failed"
    for item in (running, queued):
        current = restarted.get_job("alice", item["id"])
        assert current["engine"] == "docling"
        assert current["status"] == expected
    assert restarted.get_job("alice", expired["id"])["status"] == "expired"
    assert restarted.get_usage("alice")["used"] == (4 if ready else 3)


def test_queued_runtime_disappearance_fails_before_worker_start(
    service, available, monkeypatch
):
    result = docling_job(service)

    def unavailable(*args):
        raise ConversionError("Docling 运行环境不可用")

    monkeypatch.setattr("markitdown_web.jobs.ensure_engine_available", unavailable)
    assert service._claim() is None
    current = service.get_job("alice", result["id"])
    assert current["status"] == "failed" and current["attempts"] == 0
    assert service.get_usage("alice")["used"] == 1


@pytest.mark.parametrize("change", ["cancel", "delete", "expire", "disable", "stop"])
def test_docling_cancellation_callback_tracks_authority(
    service, available, monkeypatch, change
):
    result = docling_job(service)
    row = service._claim()
    assert service._cancelled(row) is False
    if change == "cancel":
        service.cancel("alice", result["id"])
    elif change == "delete":
        service.delete_job("alice", result["id"])
    elif change == "expire":
        update(service, result["id"], expires_at=time.time() - 1)
    elif change == "disable":
        user_update(service, is_active=0)
    else:
        service._stop.set()
    assert service._cancelled(row) is True


@pytest.mark.parametrize("change", ["cancel", "delete", "expire"])
def test_docling_physical_engine_slot_survives_public_cancellation(
    service, available, monkeypatch, change
):
    # Different users may occupy both global slots, but only one is Docling.
    entered = threading.Event()
    release = threading.Event()
    first = docling_job(service)
    row = service._claim()

    def convert(path, settings, cancelled):
        entered.set()
        assert release.wait(4)
        assert cancelled()
        return "must not publish", "<p>must not publish</p>", {}

    monkeypatch.setattr("markitdown_web.jobs.run_docling_conversion", convert)
    thread = threading.Thread(target=service._execute, args=(row,))
    thread.start()
    try:
        assert entered.wait(2)
        if change == "cancel":
            current = service.cancel("alice", first["id"])
            assert current["status"] == "failed" and current["error"] == "任务已取消"
            with pytest.raises(JobError, match="正在停止"):
                service.retry("alice", first["id"])
        elif change == "delete":
            service.delete_job("alice", first["id"])
        else:
            update(service, first["id"], expires_at=time.time() - 1)
            service.cleanup()
        second = docling_job(service, "bob")
        assert service._claim() is None
        default = service.enqueue("bob", [upload()])[0]
        default_row = service._claim()
        assert default_row["id"] == default["id"]
        assert service._claim() is None
        monkeypatch.setattr(
            "markitdown_web.jobs.run_conversion", lambda *args: ("ok", "<p>ok</p>")
        )
        service._execute(default_row)
        assert service._claim() is None
        assert service.get_usage("alice")["used"] == 1
        release.set()
        thread.join(3)
        assert not thread.is_alive()
        assert not (service.jobs_dir / first["id"] / "markdown.md").exists()
        second_row = service._claim()
        assert second_row["id"] == second["id"]
    finally:
        release.set()
        thread.join(4)


def test_docling_and_default_share_per_user_slots(service, available):
    docling_job(service)
    first = service._claim()
    default = service.enqueue("alice", [upload()])[0]
    assert service._claim() is None
    service.cancel("alice", first["id"])
    assert service._claim() is None
    assert service.get_job("alice", default["id"])["status"] == "queued"


def test_cancel_is_owned_and_keeps_quota(service, available):
    job = docling_job(service)
    for action in (service.cancel, service.retry, service.delete_job, service.get_job):
        with pytest.raises(JobError) as exc:
            action("bob", job["id"])
        assert exc.value.status_code == 404
    current = service.cancel("alice", job["id"])
    assert current["status"] == "failed"
    assert current["error"] == "任务已取消"
    assert current["engine"] == "docling"
    assert service.get_usage("alice")["used"] == 1
    with pytest.raises(JobError) as exc:
        service.cancel("alice", job["id"])
    assert exc.value.status_code == 409
    assert service.retry("alice", job["id"])["engine"] == "docling"


def test_api_engine_config_and_default_upload(client, request_headers):
    config = client.get("/api/config").json()
    assert isinstance(config["engines"], list)
    assert {item["id"] for item in config["engines"]} == {"markitdown", "docling"}
    response = client.post(
        "/api/jobs", headers=request_headers, files={"files": ("a.txt", b"hello")}
    )
    assert response.status_code == 202
    assert response.json()["jobs"][0]["engine"] == "markitdown"


@pytest.mark.parametrize("engine", ["unknown", "Docling", ""])
def test_api_rejects_invalid_engine_without_quota(client, request_headers, engine):
    response = client.post(
        "/api/jobs",
        headers=request_headers,
        data={"engine": engine},
        files={"files": ("a.txt", b"hello")},
    )
    assert response.status_code == 400
    assert client.get("/api/me").json()["usage"]["used"] == 0


def test_api_disabled_docling_rejected_without_quota(
    client, request_headers, pdf_bytes
):
    response = client.post(
        "/api/jobs",
        headers=request_headers,
        data={"engine": "docling"},
        files={"files": ("a.pdf", pdf_bytes)},
    )
    assert response.status_code == 400
    assert client.get("/api/me").json()["usage"]["used"] == 0
    assert client.get("/api/jobs").json()["jobs"] == []


def test_api_docling_cancel_checks_owner_csrf_and_preserves_engine(
    client, other_client, admin_client, request_headers, web_app, available, monkeypatch
):
    entered = threading.Event()
    release = threading.Event()

    def convert(path, settings, cancelled):
        entered.set()
        assert release.wait(4)
        return "discard", "<p>discard</p>", {}

    monkeypatch.setattr("markitdown_web.jobs.run_docling_conversion", convert)
    response = client.post(
        "/api/jobs",
        headers=request_headers,
        data={"engine": "docling"},
        files={"files": ("a.pdf", b"synthetic")},
    )
    assert response.status_code == 202
    job = response.json()["jobs"][0]
    path = f"/api/jobs/{job['id']}/cancel"
    try:
        assert entered.wait(2)
        for outsider in (other_client, admin_client):
            assert outsider.post(path, headers=headers_for(outsider)).status_code == 404
        assert (
            client.post(path, headers={"X-MarkItDown-Request": "1"}).status_code == 403
        )
        canceled = client.post(path, headers=request_headers)
        assert canceled.status_code == 200
        assert canceled.json()["engine"] == "docling"
        assert canceled.json()["error"] == "任务已取消"
        assert (
            client.post(
                f"/api/jobs/{job['id']}/retry", headers=request_headers
            ).status_code
            == 409
        )
        release.set()
        wait_for(lambda: job["id"] not in web_app.state.jobs._inflight)
        assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "failed"
    finally:
        release.set()


@pytest.mark.parametrize(
    "fields",
    [
        [("engine", (None, "docling")), ("engine", (None, "markitdown"))],
        [("engine", ("engine.txt", b"docling"))],
        [("unexpected", (None, "docling"))],
    ],
)
def test_api_rejects_duplicate_file_or_unknown_engine_fields(
    client, request_headers, fields
):
    response = client.post(
        "/api/jobs",
        headers=request_headers,
        files=[("files", ("a.txt", b"hello")), *fields],
    )
    assert response.status_code == 400
    assert client.get("/api/me").json()["usage"]["used"] == 0


def test_cancel_requires_authenticated_session(anonymous_client):
    response = anonymous_client.post(
        "/api/jobs/" + "a" * 32 + "/cancel", headers={"X-MarkItDown-Request": "1"}
    )
    assert response.status_code == 401


def test_docling_and_default_share_global_slot_after_cancel(
    service, available, monkeypatch
):
    service.settings = replace(service.settings, global_concurrency=1)
    result = docling_job(service)
    row = service._claim()
    service.cancel("alice", result["id"])
    default = service.enqueue("bob", [upload()])[0]
    assert service._claim() is None
    assert result["id"] in service._inflight_engines
    monkeypatch.setattr(
        "markitdown_web.jobs.run_docling_conversion",
        lambda *args, **kwargs: ("discard", "<p>discard</p>", {}),
    )
    service._execute(row)
    assert result["id"] not in service._inflight_engines
    assert service._claim()["id"] == default["id"]


def test_preflight_account_limit_change_is_revalidated(service, available, monkeypatch):
    def preflight(*args):
        user_update(service, max_file_bytes=1)

    monkeypatch.setattr("markitdown_web.jobs.validate_engine_uploads", preflight)
    with pytest.raises(JobError) as exc:
        docling_job(service)
    assert exc.value.status_code == 413
    assert service.get_usage("alice")["used"] == 0
    assert list(service.jobs_dir.iterdir()) == []


def test_retry_expiry_and_cancel_expiry_cannot_charge(service, available):
    result = docling_job(service)
    update(
        service, result["id"], status="failed", attempts=1, expires_at=time.time() - 1
    )
    for action in (service.retry, service.cancel):
        with pytest.raises(JobError) as exc:
            action("alice", result["id"])
        assert exc.value.status_code == 410
    assert service.get_usage("alice")["used"] == 1
