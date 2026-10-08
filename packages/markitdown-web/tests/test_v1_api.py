"""Synthetic complete API contracts for governance, snapshots and deployment."""

import pytest
from conftest import PASSWORD, REQUEST_MARKER, headers_for, login
from fastapi.testclient import TestClient

from markitdown_web.app import create_app
from markitdown_web.state import Settings


def test_reviewed_launcher_path_is_startup_only(tmp_path, monkeypatch):
    launcher = tmp_path / "reviewed-bwrap"
    monkeypatch.setenv("MARKITDOWN_SANDBOX_BWRAP", str(launcher))
    assert Settings(data_dir=tmp_path / "data").sandbox_bwrap == launcher


def test_six_character_registration_api(web_app, admin, anonymous_client):
    invite = web_app.state.auth.create_invite(admin["id"])
    payload = {
        "username": "six-user",
        "password": "12345",
        "invite_token": invite["token"],
    }
    assert (
        anonymous_client.post(
            "/api/auth/register", headers=REQUEST_MARKER, json=payload
        ).status_code
        == 422
    )
    payload["password"] = "六个中文字符"
    response = anonymous_client.post(
        "/api/auth/register", headers=REQUEST_MARKER, json=payload
    )
    assert response.status_code == 201, response.text
    assert (
        login(anonymous_client, "six-user", payload["password"])["user"]["username"]
        == "six-user"
    )


def test_versioned_admin_configuration(admin_client, client):
    assert client.get("/api/admin/settings").status_code == 403
    current = admin_client.get("/api/admin/settings").json()
    payload = {
        "expected_version": current["version"],
        "changes": {"default_daily_quota": 71, "retention_seconds": 7200},
    }
    result = admin_client.patch(
        "/api/admin/settings", headers=headers_for(admin_client), json=payload
    )
    assert result.status_code == 200, result.text
    assert result.json()["version"] == current["version"] + 1
    assert (
        admin_client.patch(
            "/api/admin/settings", headers=headers_for(admin_client), json=payload
        ).status_code
        == 409
    )
    malformed = {
        "expected_version": result.json()["version"],
        "changes": {"default_daily_quota": 72, "retention_seconds": 1},
    }
    assert (
        admin_client.patch(
            "/api/admin/settings", headers=headers_for(admin_client), json=malformed
        ).status_code
        == 422
    )
    assert admin_client.get("/api/config").json()["default_daily_quota"] == 71
    assert admin_client.get("/api/config").json()["retention_seconds"] == 7200
    assert client.get("/api/admin/audit").status_code == 403
    events = admin_client.get("/api/admin/audit").json()["events"]
    assert any(row["action"] == "settings.update" for row in events)
    assert PASSWORD not in str(events)


def test_submit_replay_and_owner_manifest(client, other_client):
    headers = {**headers_for(client), "Idempotency-Key": "v1-synthetic-upload-key"}
    files = [("files", ("中文文档.txt", b"same accepted payload", "text/plain"))]
    first = client.post("/api/jobs", headers=headers, files=files)
    assert first.status_code == 202, first.text
    again = client.post("/api/jobs", headers=headers, files=files)
    assert again.status_code == 202, again.text
    job_id = first.json()["jobs"][0]["id"]
    assert again.json()["jobs"][0]["id"] == job_id
    assert client.get("/api/me").json()["usage"]["used"] == 1
    changed = client.post(
        "/api/jobs",
        headers=headers,
        files=[("files", ("different.txt", b"new", "text/plain"))],
    )
    assert changed.status_code == 409
    result = client.get(f"/api/jobs/{job_id}/manifest")
    assert result.status_code == 200
    assert result.json()["source_sha256"]
    assert result.json()["submission_snapshot"]
    assert result.json()["native_document_json"] is False
    assert other_client.get(f"/api/jobs/{job_id}/manifest").status_code == 404


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "https://example.com/",
        "https://user:pass@example.com",
        "https://*.example.com",
        "https://example.com?x=1",
        "https://example.com:99999",
    ],
)
def test_public_origin_rejects_unsafe_values(tmp_path, origin):
    with pytest.raises(ValueError):
        Settings(data_dir=tmp_path, public_origin=origin)


def test_production_rejects_incomplete_boundary(tmp_path):
    with pytest.raises(ValueError, match="HTTPS"):
        Settings(data_dir=tmp_path, deployment_mode="production")
    with pytest.raises(ValueError, match="runtime"):
        Settings(
            data_dir=tmp_path,
            deployment_mode="production",
            public_origin="https://example.com",
            cookie_secure=True,
        )


def test_production_origin_does_not_trust_forwarded_headers(tmp_path):
    app = create_app(
        Settings(
            data_dir=tmp_path / "data",
            deployment_mode="production",
            public_origin="https://example.com",
            cookie_secure=True,
            sandbox_runtime_root=tmp_path / "runtime",
            start_workers=False,
        )
    )
    with TestClient(app, base_url="https://example.com") as browser:
        app.state.auth.bootstrap_admin("synthetic-admin", PASSWORD)
        result = browser.post(
            "/api/auth/login",
            headers={
                **REQUEST_MARKER,
                "Origin": "https://evil.example",
                "X-Forwarded-Host": "example.com",
                "X-Forwarded-Proto": "https",
            },
            json={"username": "synthetic-admin", "password": PASSWORD},
        )
        assert result.status_code == 403
        ok = browser.post(
            "/api/auth/login",
            headers={**REQUEST_MARKER, "Origin": "https://example.com"},
            json={"username": "synthetic-admin", "password": PASSWORD},
        )
        assert ok.status_code == 200
        assert "secure" in ok.headers["set-cookie"].lower()
        assert ok.headers["strict-transport-security"] == "max-age=31536000"
        assert len(ok.headers["x-request-id"]) == 32
        assert (
            browser.get("/api/health", headers={"Host": "evil.example"}).status_code
            == 400
        )
