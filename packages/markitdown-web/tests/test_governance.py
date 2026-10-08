"""Synthetic governance, recovery, audit, and six-character policy regressions."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from argon2 import extract_parameters
from argon2.low_level import Type

from markitdown_web import auth as auth_module
from markitdown_web.auth import AuthError, AuthService
from markitdown_web.governance import GovernanceError, GovernanceService
from markitdown_web.state import Database, Settings

MIB = 1024 * 1024
PASSWORD = "Synthetic existing password!"
IP = "192.0.2.40"


@pytest.fixture
def service(tmp_path):
    settings = Settings(data_dir=tmp_path)
    return AuthService(Database(settings), settings)


@pytest.fixture
def owner(service):
    return service.bootstrap_admin("synthetic-owner", PASSWORD)


@pytest.fixture
def member(service, owner):
    invitation = service.create_invite(owner["id"])
    return service.register("synthetic-member", PASSWORD, invitation["token"], IP)


def assert_error(code, function, *args, **kwargs):
    with pytest.raises((AuthError, GovernanceError)) as caught:
        function(*args, **kwargs)
    assert caught.value.status_code == code
    return caught.value


@pytest.mark.parametrize("password", ["123456", "字" * 6, "x" * 128, "\U0001f642" * 128])
def test_password_policy_accepts_six_to_128_without_hash_changes(service, password):
    owner = service.bootstrap_admin("synthetic-owner", password)
    token, _ = service.login(owner["username"], password, IP)
    assert service.get_session(token) is not None
    with service.db.connect() as connection:
        password_hash = connection.execute(
            "SELECT password_hash FROM users"
        ).fetchone()[0]
    parameters = extract_parameters(password_hash)
    assert parameters.type is Type.ID
    assert (parameters.memory_cost, parameters.time_cost, parameters.parallelism) == (
        19456,
        2,
        1,
    )
    restarted = AuthService(service.db, service.settings)
    with service.db.connect() as connection:
        assert (
            connection.execute("SELECT password_hash FROM users").fetchone()[0]
            == password_hash
        )
    assert restarted.get_session(token) is not None


@pytest.mark.parametrize("password", ["12345", "x" * 129, "\ud800" * 6, None, 123456])
def test_password_policy_rejects_boundary_and_invalid_types(service, password):
    assert_error(400, service.bootstrap_admin, "synthetic-owner", password)


def test_registration_six_character_password_and_no_automatic_login(service, owner):
    invitation = service.create_invite(owner["id"])
    user = service.register("new-member", "123456", invitation["token"], IP)
    token, _ = service.login(user["username"], "123456", IP)
    assert service.get_session(token)["user"]["id"] == user["id"]


def test_invitation_errors_distinguish_invalid_used_revoked_and_expired(
    service, owner, monkeypatch
):
    monkeypatch.setattr(auth_module.time, "time", lambda: 1000.0)
    used = service.create_invite(owner["id"])
    revoked = service.create_invite(owner["id"])
    expired = service.create_invite(owner["id"], 5)
    service.register("first-member", "123456", used["token"], IP)
    service.revoke_invite(owner["id"], revoked["id"])
    monkeypatch.setattr(auth_module.time, "time", lambda: 1005.0)
    cases = [
        ("not-a-token", "invalid"),
        (used["token"], "used"),
        (revoked["token"], "revoked"),
        (expired["token"], "expired"),
    ]
    details = set()
    for token, status in cases:
        error = assert_error(400, service.register, "next-member", "123456", token, IP)
        assert error.code == f"invite_{status}"
        assert token not in error.detail
        details.add(error.detail)
    assert len(details) == 4
    # Revoking already unusable invitations preserves useful status distinctions.
    service.revoke_invite(owner["id"], used["id"])
    service.revoke_invite(owner["id"], expired["id"])
    states = {row["id"]: row["status"] for row in service.list_invites(owner["id"])}
    assert states == {
        used["id"]: "used",
        revoked["id"]: "revoked",
        expired["id"]: "expired",
    }


def test_defaults_persist_new_accounts_only_and_deployment_is_read_only(
    service, owner, member
):
    governance = service.governance
    old = governance.get_settings(owner["id"])
    assert old["version"] == 1
    updated = governance.update_settings(
        owner["id"],
        {
            "default_daily_quota": 7,
            "default_max_file_bytes": 2 * MIB,
            "retention_seconds": 3600,
        },
        expected_version=1,
    )
    assert updated["version"] == 2
    assert updated["current"] == updated["effective"]
    assert updated["defaults"]["default_daily_quota"] == 50
    assert updated["applies_to"]["retention_seconds"] == "new_jobs"
    assert updated["updated_by"] == owner["id"]
    assert "data_dir" not in updated["deployment"]
    assert "docling_python" not in updated["deployment"]
    assert "docling_models" not in updated["deployment"]
    assert service.get_user(member["id"]) == member
    restarted = AuthService(service.db, service.settings)
    assert restarted.governance.get_settings(owner["id"]) == updated
    invitation = restarted.create_invite(owner["id"])
    created = restarted.register("later-member", "123456", invitation["token"], IP)
    assert created["daily_quota"] == 7
    assert created["max_file_bytes"] == 2 * MIB
    assert governance.current()["version"] == 2
    assert governance.current()["retention_seconds"] == 3600


@pytest.mark.parametrize(
    "changes",
    [
        {"default_daily_quota": 0},
        {"default_daily_quota": 1001},
        {"default_daily_quota": True},
        {"default_daily_quota": "10"},
        {"default_max_file_bytes": MIB - 1},
        {"default_max_file_bytes": 20 * MIB + 1},
        {"default_max_file_bytes": 1.5},
        {"retention_seconds": 3599},
        {"retention_seconds": 604801},
        {"retention_seconds": False},
        {"cookie_secure": False},
        {"docling_enabled": True},
        {"data_dir": "/private-path"},
        {"global_concurrency": 4},
        {},
        {"default_daily_quota": 9, "retention_seconds": 1},
    ],
)
def test_invalid_configuration_never_partially_applies(service, owner, changes):
    previous = service.governance.get_settings(owner["id"])
    assert_error(
        400,
        service.governance.update_settings,
        owner["id"],
        changes,
        expected_version=1,
    )
    assert service.governance.get_settings(owner["id"]) == previous
    event = service.governance.list_audit(owner["id"])[0]
    assert event["action"] == "settings.update"
    assert event["result"] == "rejected"
    assert event["error_status"] == 400


@pytest.mark.parametrize(
    "version,status", [(0, 400), (True, 400), ("1", 400), (2, 409)]
)
def test_version_errors_have_no_partial_update(service, owner, version, status):
    assert_error(
        status,
        service.governance.update_settings,
        owner["id"],
        {"default_daily_quota": 9},
        expected_version=version,
    )
    assert service.governance.current()["version"] == 1
    assert service.governance.current()["default_daily_quota"] == 50


def test_parallel_settings_updates_have_one_winner_and_stale_conflict(service, owner):
    barrier = threading.Barrier(2)

    def update(quota):
        barrier.wait(timeout=10)
        try:
            return service.governance.update_settings(
                owner["id"], {"default_daily_quota": quota}, expected_version=1
            )
        except GovernanceError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, [10, 20]))
    successes = [item for item in results if isinstance(item, dict)]
    conflicts = [item for item in results if isinstance(item, GovernanceError)]
    assert len(successes) == len(conflicts) == 1
    assert conflicts[0].status_code == 409
    assert conflicts[0].code == "version_conflict"
    assert service.governance.current()["version"] == 2
    assert (
        service.governance.current()["default_daily_quota"]
        == successes[0]["current"]["default_daily_quota"]
    )


def test_identical_settings_save_does_not_mutate_version_or_timestamps(service, owner):
    before = service.governance.get_settings(owner["id"])
    result = service.governance.update_settings(
        owner["id"], before["current"], expected_version=1
    )
    assert result == before


def test_lower_deployment_cap_overrides_persisted_business_value(service, owner):
    lower = replace(service.settings, max_file_bytes=5 * MIB)
    restarted = AuthService(service.db, lower)
    values = restarted.governance.get_settings(owner["id"])
    assert values["current"]["default_max_file_bytes"] == 20 * MIB
    assert values["effective"]["default_max_file_bytes"] == 5 * MIB
    assert values["bounds"]["default_max_file_bytes"]["max"] == 5 * MIB
    assert_error(
        400,
        restarted.governance.update_settings,
        owner["id"],
        {"default_max_file_bytes": 6 * MIB},
        expected_version=1,
    )
    # A changed unrelated setting remains editable when deployment lowered a cap.
    restarted.governance.update_settings(
        owner["id"], {"default_daily_quota": 8}, expected_version=1
    )
    invitation = restarted.create_invite(owner["id"])
    user = restarted.register("limited-member", "123456", invitation["token"], IP)
    assert user["max_file_bytes"] == 5 * MIB
    assert_error(
        400, restarted.update_user, owner["id"], user["id"], max_file_bytes=6 * MIB
    )


def test_trusted_small_deployment_limits_preserved_for_synthetic_execution(tmp_path):
    settings = Settings(data_dir=tmp_path, max_file_bytes=10, retention_seconds=0.05)
    service = AuthService(Database(settings), settings)
    owner = service.bootstrap_admin("synthetic-owner", "123456")
    assert service.governance.current()["retention_seconds"] == 0.05
    assert service.governance.current()["default_max_file_bytes"] == 10
    assert owner["max_file_bytes"] == MIB
    assert_error(
        400,
        service.governance.update_settings,
        owner["id"],
        {"retention_seconds": 0.05},
        expected_version=1,
    )


def test_governance_requires_current_active_admin(service, owner, member):
    for actor in (member["id"], "missing"):
        assert_error(403, service.governance.get_settings, actor)
        assert_error(403, service.governance.list_audit, actor)
        assert_error(
            403,
            service.governance.update_settings,
            actor,
            {"default_daily_quota": 1},
            expected_version=1,
        )
    with service.db.connect() as connection:
        connection.execute(
            "UPDATE users SET is_active = 0 WHERE id = ?", (owner["id"],)
        )
    assert_error(403, service.governance.get_settings, owner["id"])
    assert_error(403, service.governance.list_audit, owner["id"])
    assert_error(
        403,
        service.governance.update_settings,
        owner["id"],
        {"default_daily_quota": 1},
        expected_version=1,
    )


def test_audit_tracks_actor_target_change_result_request_without_secrets(
    service, owner, member
):
    request_id = uuid.uuid4().hex
    token, _ = service.login(member["username"], PASSWORD, IP)
    invitation = service.create_invite(owner["id"], request_id=request_id)
    service.update_user(
        owner["id"], member["id"], daily_quota=3, is_active=False, request_id=request_id
    )
    assert_error(
        400,
        service.update_user,
        owner["id"],
        owner["id"],
        is_active=False,
        request_id=request_id,
    )
    events = service.governance.list_audit(owner["id"])
    change = next(
        row
        for row in events
        if row["action"] == "user.update" and row["result"] == "success"
    )
    assert change["actor_id"] == owner["id"]
    assert change["target_id"] == member["id"]
    assert change["request_id"] == request_id
    assert change["before"]["daily_quota"] == 50
    assert change["after"]["daily_quota"] == 3
    assert change["before"]["is_active"] is True
    assert change["after"]["is_active"] is False
    assert change["after"]["sessions_revoked"] == 1
    assert events[0]["result"] == "rejected"
    assert events[0]["error_status"] == 400
    raw = json.dumps(events)
    for secret in (
        PASSWORD,
        token,
        invitation["token"],
        "$argon2",
        "password_hash",
        "token_hash",
        IP,
        str(service.settings.data_dir),
    ):
        assert secret not in raw
    assert service.get_session(token) is None


def test_audit_write_failure_rolls_back_business_change(
    service, owner, member, monkeypatch
):
    original = service.governance._record
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("synthetic audit storage failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(service.governance, "_record", fail_once)
    with pytest.raises(sqlite3.OperationalError):
        service.update_user(owner["id"], member["id"], daily_quota=2, is_active=False)
    assert service.get_user(member["id"]) == member
    with service.db.connect() as connection:
        assert (
            connection.execute("SELECT count(*) FROM account_revocations").fetchone()[0]
            == 0
        )
    assert service.governance.list_audit(owner["id"])[0]["result"] == "error"


def test_audit_drops_unrecognized_fields_and_untrusted_identifiers(service, owner):
    secret = "not-a-request-id-secret"
    with service.governance.mutation(
        secret, "user.update", "user", secret, request_id=secret
    ) as (_, audit):
        audit.before = {"password": secret, "document_text": secret, "status": [secret]}
        audit.after = {"password_hash": secret, "token": secret, "daily_quota": 3}
    result = service.governance.list_audit(owner["id"])[0]
    assert result["actor_id"] is None
    assert result["target_id"] is None
    assert result["before"] == {}
    assert result["after"] == {"daily_quota": 3}
    assert len(result["request_id"]) == 32
    assert secret not in json.dumps(result)


def test_audit_pagination_is_bounded_and_stable(service, owner):
    for _ in range(3):
        service.create_invite(owner["id"])
    first = service.governance.list_audit(owner["id"], limit=2)
    second = service.governance.list_audit(
        owner["id"], limit=2, before_id=first[-1]["id"]
    )
    assert len(first) == len(second) == 2
    assert min(row["id"] for row in first) > max(row["id"] for row in second)
    for limit in (0, 201, True, "2"):
        assert_error(400, service.governance.list_audit, owner["id"], limit=limit)
    for cursor in (0, -1, True, "2"):
        assert_error(400, service.governance.list_audit, owner["id"], before_id=cursor)


def test_offline_recovery_rehashes_and_revokes_all_sessions_without_other_changes(
    service, owner, member
):
    tokens = [service.login(member["username"], PASSWORD, IP)[0] for _ in range(2)]
    request_id = uuid.uuid4().hex
    recovered = service.reset_password(
        member["username"], "123456", request_id=request_id
    )
    assert recovered == member
    assert all(service.get_session(token) is None for token in tokens)
    assert_error(401, service.login, member["username"], PASSWORD, IP)
    assert service.login(member["username"], "123456", IP)[0]
    event = next(
        row
        for row in service.governance.list_audit(owner["id"])
        if row["action"] == "account.recover"
    )
    assert event["actor_id"] == "local-operator"
    assert event["target_id"] == member["id"]
    assert event["request_id"] == request_id
    assert event["after"]["sessions_revoked"] == 2
    assert "123456" not in json.dumps(event)


def test_recovery_preserves_disabled_state_and_rejects_unknown_or_short_password(
    service, owner, member
):
    service.update_user(owner["id"], member["id"], is_active=False)
    assert service.reset_password(member["username"], "123456")["is_active"] is False
    assert_error(401, service.login, member["username"], "123456", IP)
    assert_error(404, service.reset_password, "unknown-member", "123456")
    assert_error(400, service.reset_password, member["username"], "12345")
    assert_error(409, service.bootstrap_admin, "another-owner", "123456")


def test_password_reset_during_verification_cannot_create_stale_session(
    service, member, monkeypatch
):
    original = service._verify_password

    def reset_then_verify(password_hash, password):
        service.reset_password(member["username"], "123456")
        return original(password_hash, password)

    monkeypatch.setattr(service, "_verify_password", reset_then_verify)
    assert_error(401, service.login, member["username"], PASSWORD, IP)
    with service.db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


def test_account_revocation_ledger_tracks_explicit_reenable_and_migrates_disabled_accounts(
    service, owner, member, monkeypatch
):
    monkeypatch.setattr(auth_module.time, "time", lambda: 1000.0)
    service.update_user(owner["id"], member["id"], is_active=False)
    with service.db.connect() as connection:
        row = dict(connection.execute("SELECT * FROM account_revocations").fetchone())
    assert row == {
        "user_id": member["id"],
        "disabled_at": 1000.0,
        "is_active": 0,
        "updated_at": 1000.0,
    }
    monkeypatch.setattr(auth_module.time, "time", lambda: 1010.0)
    service.update_user(owner["id"], member["id"], is_active=True)
    with service.db.connect() as connection:
        row = dict(connection.execute("SELECT * FROM account_revocations").fetchone())
        assert row["disabled_at"] == 1000.0
        assert row["updated_at"] == 1010.0
        assert row["is_active"] == 1
        # Simulate upgrading a pre-ledger database with a disabled account.
        connection.execute("DROP TABLE account_revocations")
        connection.execute(
            "UPDATE users SET is_active = 0 WHERE id = ?", (member["id"],)
        )
    GovernanceService(service.db, service.settings)
    with service.db.connect() as connection:
        row = dict(connection.execute("SELECT * FROM account_revocations").fetchone())
        assert row["is_active"] == 0
        assert row["user_id"] == member["id"]
