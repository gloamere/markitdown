"""Real-session, authorization, administrator, and private job API integration."""

from __future__ import annotations

import importlib
import time

import pytest
from conftest import BASE_URL, PASSWORD, REQUEST_MARKER, headers_for, login, wait_job
from fastapi.testclient import TestClient
from test_api import failed_result, successful_result, upload

from markitdown_web.app import create_app
from markitdown_web.state import Settings

jobs_module = importlib.import_module("markitdown_web.jobs")
COOKIE = "markitdown_session"
MIB = 1024 * 1024


def test_registration_requires_valid_single_use_invitation(
    anonymous_client, admin_client
):
    created = admin_client.post(
        "/api/admin/invites", headers=headers_for(admin_client), json={"ttl_hours": 24}
    )
    assert created.status_code == 201, created.text
    invite = created.json()
    assert invite["token"] and invite["id"]
    assert time.time() + 23 * 3600 < invite["expires_at"] <= time.time() + 24 * 3600
    registration = {
        "username": "invited-user",
        "password": PASSWORD,
        "invite_token": invite["token"],
    }
    response = anonymous_client.post(
        "/api/auth/register", headers=REQUEST_MARKER, json=registration
    )
    assert response.status_code == 201, response.text
    assert response.json()["user"]["username"] == "invited-user"
    assert response.json()["user"]["is_admin"] is False
    assert "password" not in response.text
    assert invite["token"] not in response.text
    assert "set-cookie" not in response.headers
    assert anonymous_client.get("/api/me").status_code == 401
    replay = anonymous_client.post(
        "/api/auth/register",
        headers=REQUEST_MARKER,
        json={**registration, "username": "second-user"},
    )
    assert replay.status_code == 400
    current = login(anonymous_client, "invited-user")
    assert current["user"]["username"] == "invited-user"
    assert current["csrf_token"] and current["usage"]["used"] == 0
    assert anonymous_client.get("/api/config").json()["has_admin"] is True


@pytest.mark.parametrize("invite_token", ["x" * 43, "", "not-a-real-invite"])
def test_invalid_invites_cannot_register(anonymous_client, admin, invite_token):
    response = anonymous_client.post(
        "/api/auth/register",
        headers=REQUEST_MARKER,
        json={
            "username": "uninvited-user",
            "password": PASSWORD,
            "invite_token": invite_token,
        },
    )
    assert response.status_code in {400, 422}
    assert anonymous_client.get("/api/me").status_code == 401


def test_login_cookie_flags_and_rotation(client, test_user):
    old_token = client.cookies[COOKIE]
    old_csrf = client.get("/api/me").json()["csrf_token"]
    response = client.post(
        "/api/auth/login",
        headers=REQUEST_MARKER,
        json={"username": test_user["username"], "password": PASSWORD},
    )
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "path=/" in cookie
    assert "max-age=43200" in cookie
    assert "secure" not in cookie
    assert client.cookies[COOKIE] != old_token
    assert response.json()["csrf_token"] != old_csrf
    assert "password" not in response.text
    assert client.cookies[COOKIE] not in response.text
    assert (
        client.get("/api/me", headers={"Cookie": f"{COOKIE}={old_token}"}).status_code
        == 401
    )
    assert client.get("/api/me").status_code == 200
    stale = client.post(
        "/api/auth/logout", headers={**REQUEST_MARKER, "X-CSRF-Token": old_csrf}
    )
    assert stale.status_code == 403
    assert client.get("/api/me").status_code == 200


def test_secure_cookie_is_available_for_tls_deployments(tmp_path):
    application = create_app(Settings(data_dir=tmp_path / "secure", cookie_secure=True))
    with TestClient(
        application, base_url=BASE_URL.replace("http:", "https:")
    ) as secure_client:
        application.state.auth.bootstrap_admin("secure-admin", PASSWORD)
        response = secure_client.post(
            "/api/auth/login",
            headers=REQUEST_MARKER,
            json={"username": "secure-admin", "password": PASSWORD},
        )
        assert response.status_code == 200
        assert "; secure" in response.headers["set-cookie"].lower()
        assert secure_client.get("/api/me").status_code == 200


def test_logout_revokes_session_and_clears_cookie(client, request_headers):
    token = client.cookies[COOKIE]
    response = client.post("/api/auth/logout", headers=request_headers)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert COOKIE not in client.cookies
    assert client.get("/api/me").status_code == 401
    assert (
        client.get("/api/me", headers={"Cookie": f"{COOKIE}={token}"}).status_code
        == 401
    )
    assert client.post("/api/auth/logout", headers=request_headers).status_code == 401


def test_expired_sessions_are_rejected(client, web_app):
    with web_app.state.db.connect() as connection:
        connection.execute("UPDATE sessions SET expires_at = ?", (time.time() - 1,))
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/jobs").status_code == 401


@pytest.mark.parametrize(
    "username,password",
    [("test-user", "wrong-password-123!"), ("missing-user", PASSWORD)],
)
def test_invalid_logins_are_generic(anonymous_client, test_user, username, password):
    response = anonymous_client.post(
        "/api/auth/login",
        headers=REQUEST_MARKER,
        json={"username": username, "password": password},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "用户名或密码不正确"}
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("GET", "/api/me", None),
        ("GET", "/api/jobs", None),
        ("GET", "/api/jobs/" + "a" * 32, None),
        ("GET", "/api/jobs/" + "a" * 32 + "/download", None),
        ("POST", "/api/jobs", None),
        ("POST", "/api/jobs/" + "a" * 32 + "/retry", None),
        ("DELETE", "/api/jobs/" + "a" * 32, None),
        ("POST", "/api/jobs/archive", {"job_ids": ["a" * 32]}),
        ("GET", "/api/admin/users", None),
        ("GET", "/api/admin/invites", None),
        ("POST", "/api/admin/invites", {"ttl_hours": 24}),
    ],
)
def test_private_routes_require_authentication(anonymous_client, method, path, payload):
    response = anonymous_client.request(
        method, path, headers=REQUEST_MARKER, json=payload
    )
    assert response.status_code == 401, response.text


def test_legacy_anonymous_conversion_endpoint_is_removed(anonymous_client):
    response = anonymous_client.post(
        "/api/convert",
        headers=REQUEST_MARKER,
        files={"files": ("sample.txt", b"sample")},
    )
    assert response.status_code == 404


@pytest.mark.parametrize("csrf", [None, "", "wrong-token"])
@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("POST", "/api/jobs", None),
        ("POST", "/api/jobs/" + "a" * 32 + "/retry", None),
        ("DELETE", "/api/jobs/" + "a" * 32, None),
        ("POST", "/api/jobs/archive", {"job_ids": ["a" * 32]}),
        ("POST", "/api/auth/logout", None),
    ],
)
def test_authenticated_mutations_require_session_csrf(
    client, csrf, method, path, payload
):
    headers = (
        REQUEST_MARKER if csrf is None else {**REQUEST_MARKER, "X-CSRF-Token": csrf}
    )
    assert (
        client.request(method, path, headers=headers, json=payload).status_code == 403
    )


def test_other_users_csrf_token_does_not_authorize_mutation(client, other_client):
    assert (
        client.post("/api/auth/logout", headers=headers_for(other_client)).status_code
        == 403
    )
    assert client.get("/api/me").status_code == 200


@pytest.mark.parametrize("actor", ["other", "admin"])
def test_all_job_routes_hide_other_owners_even_from_admin(
    client, request_headers, other_client, admin_client, actor, monkeypatch
):
    monkeypatch.setattr(
        jobs_module,
        "run_conversion",
        lambda path, suffix: ("PRIVATE_OWNER_CONTENT", "<p>PRIVATE_OWNER_CONTENT</p>"),
    )
    owner_job = successful_result(
        client, upload(client, request_headers, "private.txt", b"PRIVATE_OWNER_CONTENT")
    )
    intruder = other_client if actor == "other" else admin_client
    headers = headers_for(intruder)
    own_job = successful_result(
        intruder, upload(intruder, headers, "own.txt", b"other")
    )
    listing = intruder.get("/api/jobs")
    assert listing.status_code == 200
    assert owner_job["id"] not in listing.text
    assert {job["id"] for job in listing.json()["jobs"]} == {own_job["id"]}
    assert "markdown" not in listing.json()["jobs"][0]
    assert "html" not in listing.json()["jobs"][0]
    prefix = f"/api/jobs/{owner_job['id']}"
    requests = [
        ("GET", prefix, None),
        ("GET", prefix + "/download", None),
        ("POST", prefix + "/retry", None),
        ("DELETE", prefix, None),
        ("POST", "/api/jobs/archive", {"job_ids": [owner_job["id"]]}),
        ("POST", "/api/jobs/archive", {"job_ids": [own_job["id"], owner_job["id"]]}),
    ]
    for method, path, payload in requests:
        response = intruder.request(method, path, headers=headers, json=payload)
        assert response.status_code == 404, (method, path, response.text)
        assert "PRIVATE_OWNER_CONTENT" not in response.text
        assert "private.txt" not in response.text
    # A rejected foreign deletion must leave the owner's result intact.
    assert client.get(prefix + "/download").text == owner_job["markdown"]


def test_retry_reuses_owned_source_and_charges_quota(
    client, request_headers, monkeypatch
):
    def fail(path, suffix):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(jobs_module, "run_conversion", fail)
    job = failed_result(
        client, upload(client, request_headers, "retry.txt", b"retry content")
    )
    assert client.get("/api/me").json()["usage"]["used"] == 1
    monkeypatch.setattr(
        jobs_module,
        "run_conversion",
        lambda path, suffix: (path.read_text(), "<p>retry content</p>"),
    )
    retried = client.post(f"/api/jobs/{job['id']}/retry", headers=request_headers)
    assert retried.status_code == 200, retried.text
    assert retried.json()["id"] == job["id"]
    complete = wait_job(client, job)
    assert complete["status"] == "succeeded"
    assert complete["attempts"] == 2
    assert complete["markdown"] == "retry content"
    assert client.get("/api/me").json()["usage"]["used"] == 2
    assert (
        client.post(f"/api/jobs/{job['id']}/retry", headers=request_headers).status_code
        == 409
    )


def test_expired_jobs_cannot_return_content(
    client, request_headers, web_app, monkeypatch
):
    monkeypatch.setattr(
        jobs_module,
        "run_conversion",
        lambda path, suffix: (
            "expired private content",
            "<p>expired private content</p>",
        ),
    )
    job = successful_result(
        client, upload(client, request_headers, "expired.txt", b"data")
    )
    with web_app.state.db.connect() as connection:
        connection.execute(
            "UPDATE jobs SET expires_at = ? WHERE id = ?", (time.time() - 1, job["id"])
        )
    prefix = f"/api/jobs/{job['id']}"
    for method, path, body in [
        ("GET", prefix, None),
        ("GET", prefix + "/download", None),
        ("POST", prefix + "/retry", None),
        ("POST", "/api/jobs/archive", {"job_ids": [job["id"]]}),
    ]:
        response = client.request(method, path, headers=request_headers, json=body)
        assert response.status_code in {404, 410}
        assert "expired private content" not in response.text
    assert client.get("/api/jobs").json()["jobs"][0]["status"] == "expired"
    web_app.state.jobs.cleanup()
    assert not (web_app.state.settings.data_dir / "jobs" / job["id"]).exists()


def test_admin_updates_limits_and_disables_existing_sessions(
    client, request_headers, admin_client, test_user
):
    response = admin_client.patch(
        f"/api/admin/users/{test_user['id']}",
        headers=headers_for(admin_client),
        json={"daily_quota": 2, "max_file_bytes": MIB},
    )
    assert response.status_code == 200, response.text
    me = client.get("/api/me").json()
    assert me["user"]["daily_quota"] == me["usage"]["daily_quota"] == 2
    assert me["user"]["max_file_bytes"] == me["usage"]["max_file_bytes"] == MIB
    too_large = upload(client, request_headers, "over-user-limit.txt", b"x" * (MIB + 1))
    assert too_large.status_code == 202
    assert too_large.json()["jobs"] == []
    assert too_large.json()["errors"]
    disabled = admin_client.patch(
        f"/api/admin/users/{test_user['id']}",
        headers=headers_for(admin_client),
        json={"is_active": False},
    )
    assert disabled.status_code == 200
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/jobs").status_code == 401
    assert upload(client, request_headers, "denied.txt", b"data").status_code == 401
    denied_login = client.post(
        "/api/auth/login",
        headers=REQUEST_MARKER,
        json={"username": test_user["username"], "password": PASSWORD},
    )
    assert denied_login.status_code == 401


def test_batch_quota_failure_is_atomic(
    client, request_headers, admin_client, test_user, monkeypatch
):
    monkeypatch.setattr(
        jobs_module,
        "run_conversion",
        lambda path, suffix: ("converted", "<p>converted</p>"),
    )
    response = admin_client.patch(
        f"/api/admin/users/{test_user['id']}",
        headers=headers_for(admin_client),
        json={"daily_quota": 2},
    )
    assert response.status_code == 200
    files = [("files", (f"part-{number}.txt", b"data")) for number in range(3)]
    rejected = client.post("/api/jobs", headers=request_headers, files=files)
    assert rejected.status_code == 429
    assert client.get("/api/jobs").json()["jobs"] == []
    assert client.get("/api/me").json()["usage"]["used"] == 0
    accepted = client.post("/api/jobs", headers=request_headers, files=files[:2])
    assert accepted.status_code == 202
    assert all(
        wait_job(client, job)["status"] == "succeeded"
        for job in accepted.json()["jobs"]
    )
    assert client.get("/api/me").json()["usage"]["remaining"] == 0
    assert upload(client, request_headers, "over-quota.txt", b"data").status_code == 429
    assert len(client.get("/api/jobs").json()["jobs"]) == 2


def test_nonadmins_cannot_manage_users_or_invites(client, request_headers, test_user):
    for method, path, body in [
        ("GET", "/api/admin/users", None),
        ("GET", "/api/admin/invites", None),
        ("POST", "/api/admin/invites", {"ttl_hours": 24}),
        ("DELETE", "/api/admin/invites/" + "a" * 32, None),
        ("PATCH", f"/api/admin/users/{test_user['id']}", {"daily_quota": 999}),
    ]:
        assert (
            client.request(method, path, headers=request_headers, json=body).status_code
            == 403
        )


def test_admin_invite_listing_hides_tokens_and_revoke_prevents_signup(
    anonymous_client, admin_client
):
    headers = headers_for(admin_client)
    created = admin_client.post(
        "/api/admin/invites", headers=headers, json={"ttl_hours": 24}
    )
    assert created.status_code == 201
    invite = created.json()
    listing = admin_client.get("/api/admin/invites")
    assert listing.status_code == 200
    assert invite["id"] in {entry["id"] for entry in listing.json()["invites"]}
    assert invite["token"] not in listing.text
    assert "token_hash" not in listing.text
    assert (
        admin_client.delete(
            f"/api/admin/invites/{invite['id']}", headers=headers
        ).status_code
        == 200
    )
    response = anonymous_client.post(
        "/api/auth/register",
        headers=REQUEST_MARKER,
        json={
            "username": "revoked-user",
            "password": PASSWORD,
            "invite_token": invite["token"],
        },
    )
    assert response.status_code == 400
    users = admin_client.get("/api/admin/users")
    assert users.status_code == 200
    assert "password" not in users.text and "token" not in users.text
    assert "revoked-user" not in {entry["username"] for entry in users.json()["users"]}


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("POST", "/api/admin/invites", {"ttl_hours": 24}),
        ("DELETE", "/api/admin/invites/" + "a" * 32, None),
        ("PATCH", "/api/admin/users/" + "a" * 32, {"daily_quota": 1}),
    ],
)
def test_admin_mutations_also_require_csrf(admin_client, method, path, payload):
    assert (
        admin_client.request(
            method, path, headers=REQUEST_MARKER, json=payload
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"daily_quota": 0},
        {"daily_quota": True},
        {"daily_quota": 1001},
        {"max_file_bytes": MIB - 1},
        {"max_file_bytes": 20 * MIB + 1},
        {"is_active": "false"},
        {"is_admin": True},
    ],
)
def test_admin_limits_validate_strict_types_and_bounds(
    admin_client, test_user, payload
):
    response = admin_client.patch(
        f"/api/admin/users/{test_user['id']}",
        headers=headers_for(admin_client),
        json=payload,
    )
    assert response.status_code == 422


def test_invalid_auth_input_does_not_echo_secrets(anonymous_client):
    secret = "SYNTHETIC_SECRET_DO_NOT_ECHO"
    response = anonymous_client.post(
        "/api/auth/register",
        headers=REQUEST_MARKER,
        json={"username": "x", "password": {"secret": secret}, "invite_token": secret},
    )
    assert response.status_code == 422
    assert secret not in response.text
    assert "password" not in response.text
