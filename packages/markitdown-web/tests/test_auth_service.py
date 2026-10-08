"""Synthetic, isolated security regression tests for invitation-only accounts."""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from argon2 import extract_parameters
from argon2.low_level import Type

from markitdown_web import auth as auth_module
from markitdown_web.auth import AuthError, AuthService
from markitdown_web.state import Database, Settings

PASSWORD = "Synthetic test passphrase!"
OTHER_PASSWORD = "Another synthetic passphrase!"
IP = "192.0.2.10"


@pytest.fixture
def auth_service(tmp_path):
    settings = Settings(data_dir=tmp_path)
    return AuthService(Database(settings), settings)


@pytest.fixture
def admin(auth_service):
    return auth_service.bootstrap_admin("test_admin", PASSWORD)


@pytest.fixture
def member(auth_service, admin):
    invite = auth_service.create_invite(admin["id"])
    return auth_service.register("test_member", PASSWORD, invite["token"], IP)


def assert_error(status, call, *args, **kwargs):
    with pytest.raises(AuthError) as raised:
        call(*args, **kwargs)
    assert raised.value.status_code == status
    return raised.value


def test_bootstrap_is_once_only_and_users_are_public(auth_service):
    assert auth_service.has_admin() is False
    user = auth_service.bootstrap_admin("  TEST_Admin  ", PASSWORD)
    assert auth_service.has_admin() is True
    assert user["username"] == "test_admin"
    assert user["is_admin"] is True
    assert user["is_active"] is True
    assert user["daily_quota"] == 50
    assert user["max_file_bytes"] == 20 * 1024 * 1024
    assert set(user) == {
        "id",
        "username",
        "is_admin",
        "is_active",
        "daily_quota",
        "max_file_bytes",
        "created_at",
    }
    assert auth_service.get_user(user["id"]) == user
    assert auth_service.get_user("does-not-exist") is None
    assert_error(409, auth_service.bootstrap_admin, "second_admin", PASSWORD)
    with auth_service.db.connect() as connection:
        stored = connection.execute("SELECT * FROM users").fetchone()
    assert stored["password_hash"] != PASSWORD
    params = extract_parameters(stored["password_hash"])
    assert params.type is Type.ID
    assert (params.memory_cost, params.time_cost, params.parallelism) == (19456, 2, 1)


def test_disabled_existing_admin_does_not_reopen_bootstrap(auth_service, admin):
    with auth_service.db.connect() as connection:
        connection.execute(
            "UPDATE users SET is_active = 0 WHERE id = ?", (admin["id"],)
        )
    assert auth_service.has_admin() is True
    assert_error(409, auth_service.bootstrap_admin, "second_admin", PASSWORD)


@pytest.mark.parametrize(
    "username",
    ["ab", "a" * 33, "a" * 100000, "a b", "用户", "Kelvin", "x@y.com", "a\x00b"],
)
def test_invalid_usernames_are_rejected(auth_service, username):
    assert_error(400, auth_service.bootstrap_admin, username, PASSWORD)


@pytest.mark.parametrize("password", ["short", "a" * 129, "x" * 100000, "\ud800" * 12])
def test_invalid_passwords_are_rejected(auth_service, password):
    assert_error(400, auth_service.bootstrap_admin, "valid_user", password)


def test_unicode_passwords_are_allowed_and_never_normalized(auth_service):
    password = "安全的测试口令字符甲乙丙丁戊"
    auth_service.bootstrap_admin("test_admin", password)
    token, _ = auth_service.login("test_admin", password, IP)
    assert auth_service.get_session(token) is not None


def test_registration_requires_single_use_invite_and_does_not_login(
    auth_service, admin
):
    invite = auth_service.create_invite(admin["id"])
    user = auth_service.register("NEW_User", PASSWORD, invite["token"], IP)
    assert user["username"] == "new_user"
    assert user["is_admin"] is False
    with auth_service.db.connect() as connection:
        stored = connection.execute("SELECT * FROM invites").fetchone()
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    assert stored["token_hash"] == hashlib.sha256(invite["token"].encode()).hexdigest()
    assert stored["token_hash"] != invite["token"]
    assert stored["used_by"] == user["id"]
    assert stored["used_at"] is not None
    assert_error(
        400, auth_service.register, "second_user", PASSWORD, invite["token"], IP
    )
    metadata = auth_service.list_invites(admin["id"])
    assert len(metadata) == 1
    assert metadata[0]["status"] == "used"
    assert "token" not in metadata[0]
    assert "token_hash" not in metadata[0]
    assert invite["token"] not in repr(metadata)


def test_duplicate_username_rolls_back_invite_consumption(auth_service, admin):
    invite = auth_service.create_invite(admin["id"])
    assert_error(
        400, auth_service.register, "TEST_Admin", PASSWORD, invite["token"], IP
    )
    user = auth_service.register("actual_member", PASSWORD, invite["token"], IP)
    assert user["username"] == "actual_member"


def test_invites_expire_at_boundary_and_revocation_is_idempotent(
    auth_service, admin, monkeypatch
):
    monkeypatch.setattr(auth_module.time, "time", lambda: 1000.0)
    expired = auth_service.create_invite(admin["id"], ttl_seconds=5)
    revoked = auth_service.create_invite(admin["id"])
    assert expired["expires_at"] == 1005.0
    auth_service.revoke_invite(admin["id"], revoked["id"])
    auth_service.revoke_invite(admin["id"], revoked["id"])
    assert_error(
        400, auth_service.register, "revoked_user", PASSWORD, revoked["token"], IP
    )
    monkeypatch.setattr(auth_module.time, "time", lambda: 1005.0)
    assert_error(
        400, auth_service.register, "expired_user", PASSWORD, expired["token"], IP
    )
    states = {
        item["id"]: item["status"] for item in auth_service.list_invites(admin["id"])
    }
    assert states[expired["id"]] == "expired"
    assert states[revoked["id"]] == "revoked"
    assert_error(404, auth_service.revoke_invite, admin["id"], "not-found")


def test_parallel_registration_consumes_invite_once(auth_service, admin, monkeypatch):
    invite = auth_service.create_invite(admin["id"])
    barrier = threading.Barrier(2)
    original_hash = auth_service._passwords.hash

    def simultaneous_hash(_hasher, password):
        result = original_hash(password)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(type(auth_service._passwords), "hash", simultaneous_hash)

    def register(username):
        try:
            return auth_service.register(username, PASSWORD, invite["token"], IP)
        except AuthError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(register, ["first_user", "second_user"]))
    successes = [item for item in outcomes if isinstance(item, dict)]
    failures = [item for item in outcomes if isinstance(item, AuthError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].status_code == 400
    with auth_service.db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 2
        used_by = connection.execute("SELECT used_by FROM invites").fetchone()[0]
    assert used_by == successes[0]["id"]


@pytest.mark.parametrize("change", ["expire", "revoke"])
def test_invite_is_rechecked_after_password_hashing(
    auth_service, admin, monkeypatch, change
):
    monkeypatch.setattr(auth_module.time, "time", lambda: 1000.0)
    invite = auth_service.create_invite(admin["id"], ttl_seconds=10)
    original_hash = auth_service._passwords.hash

    def change_invite_then_hash(_hasher, password):
        if change == "expire":
            monkeypatch.setattr(auth_module.time, "time", lambda: 1010.0)
        else:
            auth_service.revoke_invite(admin["id"], invite["id"])
        return original_hash(password)

    monkeypatch.setattr(type(auth_service._passwords), "hash", change_invite_then_hash)
    assert_error(
        400, auth_service.register, "new_member", PASSWORD, invite["token"], IP
    )
    with auth_service.db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 1
        assert connection.execute("SELECT used_at FROM invites").fetchone()[0] is None


def test_parallel_bootstrap_creates_only_one_admin(auth_service, monkeypatch):
    barrier = threading.Barrier(2)
    original_hash = auth_service._passwords.hash

    def simultaneous_hash(_hasher, password):
        result = original_hash(password)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(type(auth_service._passwords), "hash", simultaneous_hash)

    def bootstrap(username):
        try:
            return auth_service.bootstrap_admin(username, PASSWORD)
        except AuthError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(bootstrap, ["first_admin", "second_admin"]))
    assert sum(isinstance(item, dict) for item in outcomes) == 1
    assert sum(isinstance(item, AuthError) for item in outcomes) == 1
    with auth_service.db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 1


def test_sessions_are_hashed_csrf_bound_absolute_and_revocable(
    auth_service, member, monkeypatch
):
    monkeypatch.setattr(auth_module.time, "time", lambda: 1000.0)
    token, session = auth_service.login("TEST_Member", PASSWORD, IP)
    assert len(token) == 43
    assert session["user"] == member
    assert (
        session["csrf_token"] == hashlib.sha256(("csrf:" + token).encode()).hexdigest()
    )
    with auth_service.db.connect() as connection:
        stored = connection.execute("SELECT * FROM sessions").fetchone()
    assert stored["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in repr(dict(stored))
    assert stored["expires_at"] == 1000 + 12 * 60 * 60
    monkeypatch.setattr(auth_module.time, "time", lambda: stored["expires_at"] - 1)
    assert auth_service.get_session(token) == session
    with auth_service.db.connect() as connection:
        assert connection.execute("SELECT expires_at FROM sessions").fetchone()[0] == (
            stored["expires_at"]
        )
    monkeypatch.setattr(auth_module.time, "time", lambda: stored["expires_at"])
    assert auth_service.get_session(token) is None
    with auth_service.db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    token, session = auth_service.login("test_member", PASSWORD, IP)
    another_token, another_session = auth_service.login("test_member", PASSWORD, IP)
    assert session["csrf_token"] != another_session["csrf_token"]
    auth_service.logout(token)
    auth_service.logout(token)
    assert auth_service.get_session(token) is None
    assert auth_service.get_session(another_token) == another_session
    assert auth_service.get_session("invalid") is None
    assert auth_service.get_session("x" * 100000) is None


def test_login_errors_are_generic_and_unknown_users_do_dummy_verification(
    auth_service, admin, monkeypatch
):
    real_verify = auth_service._verify_password
    verified_hashes = []

    def record_verification(password_hash, password):
        verified_hashes.append(password_hash)
        return real_verify(password_hash, password)

    monkeypatch.setattr(auth_service, "_verify_password", record_verification)
    wrong_password = assert_error(
        401, auth_service.login, admin["username"], OTHER_PASSWORD, IP
    )
    missing_user = assert_error(
        401, auth_service.login, "missing_user", OTHER_PASSWORD, IP
    )
    assert wrong_password.detail == missing_user.detail
    assert len(verified_hashes) == 2
    assert verified_hashes[0] != auth_service._dummy_hash
    assert verified_hashes[1] == auth_service._dummy_hash
    malformed = assert_error(401, auth_service.login, "a" * 100000, "x" * 100000, IP)
    assert malformed.detail == missing_user.detail
    assert len(verified_hashes) == 2


def test_disabling_member_revokes_all_sessions_and_reenable_requires_login(
    auth_service, admin, member
):
    tokens = [auth_service.login(member["username"], PASSWORD, IP)[0] for _ in range(2)]
    updated = auth_service.update_user(
        admin["id"], member["id"], daily_quota=3, max_file_bytes=1024 * 1024
    )
    assert updated["daily_quota"] == 3
    assert updated["max_file_bytes"] == 1024 * 1024
    assert auth_service.get_session(tokens[0])["user"] == updated
    updated = auth_service.update_user(admin["id"], member["id"], is_active=False)
    assert updated["is_active"] is False
    assert all(auth_service.get_session(token) is None for token in tokens)
    with auth_service.db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    assert_error(401, auth_service.login, member["username"], PASSWORD, IP)
    auth_service.update_user(admin["id"], member["id"], is_active=True)
    assert all(auth_service.get_session(token) is None for token in tokens)
    new_token, _ = auth_service.login(member["username"], PASSWORD, IP)
    assert auth_service.get_session(new_token) is not None


def test_every_session_lookup_checks_active_user(auth_service, member):
    token, _ = auth_service.login(member["username"], PASSWORD, IP)
    with auth_service.db.connect() as connection:
        connection.execute(
            "UPDATE users SET is_active = 0 WHERE id = ?", (member["id"],)
        )
    assert auth_service.get_session(token) is None


def test_disable_during_password_check_cannot_create_session(
    auth_service, admin, member, monkeypatch
):
    real_verify = auth_service._verify_password

    def disable_then_verify(password_hash, password):
        auth_service.update_user(admin["id"], member["id"], is_active=False)
        return real_verify(password_hash, password)

    monkeypatch.setattr(auth_service, "_verify_password", disable_then_verify)
    assert_error(401, auth_service.login, member["username"], PASSWORD, IP)
    with auth_service.db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


def test_member_cannot_use_admin_operations(auth_service, admin, member):
    invite = auth_service.create_invite(admin["id"])
    assert_error(403, auth_service.create_invite, member["id"])
    assert_error(403, auth_service.list_invites, member["id"])
    assert_error(403, auth_service.revoke_invite, member["id"], invite["id"])
    assert_error(403, auth_service.list_users, member["id"])
    assert_error(
        403, auth_service.update_user, member["id"], admin["id"], is_active=False
    )
    assert_error(403, auth_service.create_invite, "not-found")
    with auth_service.db.connect() as connection:
        connection.execute(
            "UPDATE users SET is_active = 0 WHERE id = ?", (admin["id"],)
        )
    assert_error(403, auth_service.create_invite, admin["id"])


def test_admin_cannot_disable_self_and_limits_are_enforced(auth_service, admin, member):
    assert_error(
        400, auth_service.update_user, admin["id"], admin["id"], is_active=False
    )
    assert auth_service.get_user(admin["id"])["is_active"] is True
    for quota in (0, -1, 1001, True, 1.5, "10"):
        assert_error(
            400, auth_service.update_user, admin["id"], member["id"], daily_quota=quota
        )
    for maximum in (0, 1024 * 1024 - 1, 20 * 1024 * 1024 + 1, True, 1.5):
        assert_error(
            400,
            auth_service.update_user,
            admin["id"],
            member["id"],
            max_file_bytes=maximum,
        )
    assert_error(400, auth_service.update_user, admin["id"], member["id"], is_active=1)
    assert_error(404, auth_service.update_user, admin["id"], "not-found")
    assert len(auth_service.list_users(admin["id"])) == 2


def test_account_rate_limit_persists_across_instances_and_expires(
    auth_service, admin, monkeypatch
):
    monkeypatch.setattr(auth_module.time, "time", lambda: 1000.0)
    calls = []

    def fail_verification(*args):
        calls.append(args)
        return False

    monkeypatch.setattr(auth_service, "_verify_password", fail_verification)
    for index in range(10):
        assert_error(
            401, auth_service.login, "TEST_Admin", OTHER_PASSWORD, f"192.0.2.{index}"
        )
    assert_error(429, auth_service.login, "test_admin", PASSWORD, "192.0.2.250")
    assert len(calls) == 10
    restarted = AuthService(auth_service.db, auth_service.settings)
    assert_error(429, restarted.login, "  test_ADMIN ", PASSWORD, "192.0.2.251")
    monkeypatch.setattr(auth_module.time, "time", lambda: 1900.0)
    token, _ = restarted.login("test_admin", PASSWORD, IP)
    assert restarted.get_session(token) is not None
    with auth_service.db.connect() as connection:
        assert (
            connection.execute("SELECT count(*) FROM auth_attempts").fetchone()[0] == 0
        )


def test_ip_rate_limit_combines_login_and_registration(
    auth_service, admin, monkeypatch
):
    monkeypatch.setattr(auth_service, "_verify_password", lambda *args: False)
    for index in range(15):
        assert_error(401, auth_service.login, f"missing_{index}", OTHER_PASSWORD, IP)
        assert_error(
            400, auth_service.register, f"register_{index}", PASSWORD, "bad-invite", IP
        )
    assert_error(429, auth_service.login, admin["username"], PASSWORD, IP)
    assert_error(429, auth_service.register, "another_user", PASSWORD, "bad-invite", IP)
    with auth_service.db.connect() as connection:
        rows = connection.execute("SELECT * FROM auth_attempts").fetchall()
    assert len(rows) == 30
    assert IP not in repr([dict(row) for row in rows])
    assert all(len(row["ip_key"]) == 64 for row in rows)
    assert all(len(row["username_key"]) == 64 for row in rows)


def test_parallel_attempts_cannot_bypass_account_limit(auth_service, monkeypatch):
    monkeypatch.setattr(auth_service, "_verify_password", lambda *args: False)

    def fail_login(index):
        try:
            auth_service.login("missing_user", PASSWORD, f"192.0.2.{index}")
        except AuthError as error:
            return error.status_code
        return 200

    with ThreadPoolExecutor(max_workers=12) as pool:
        statuses = list(pool.map(fail_login, range(20)))
    assert statuses.count(401) == 10
    assert statuses.count(429) == 10


def test_successful_logins_do_not_fill_failure_quota(auth_service, admin):
    for _ in range(12):
        token, _ = auth_service.login(admin["username"], PASSWORD, IP)
        auth_service.logout(token)
    with auth_service.db.connect() as connection:
        assert (
            connection.execute("SELECT count(*) FROM auth_attempts").fetchone()[0] == 0
        )


def test_successful_logins_evict_oldest_session_at_twenty_per_user(
    auth_service, admin, member, monkeypatch
):
    # Identical timestamps also verify deterministic oldest-first eviction.
    monkeypatch.setattr(auth_module.time, "time", lambda: 1000.0)
    admin_token, _ = auth_service.login(admin["username"], PASSWORD, IP)
    member_tokens = [
        auth_service.login(member["username"], PASSWORD, IP)[0] for _ in range(21)
    ]
    assert auth_service.get_session(member_tokens[0]) is None
    assert all(
        auth_service.get_session(token) is not None for token in member_tokens[1:]
    )
    assert auth_service.get_session(admin_token) is not None
    with auth_service.db.connect() as connection:
        stored = connection.execute(
            "SELECT token_hash FROM sessions WHERE user_id = ?", (member["id"],)
        ).fetchall()
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 21
    assert len(stored) == 20
    assert {row["token_hash"] for row in stored} == {
        hashlib.sha256(token.encode()).hexdigest() for token in member_tokens[1:]
    }
    assert all(len(token) == 43 for token in member_tokens)


def test_stale_attempt_cleanup_is_bounded(auth_service, monkeypatch):
    with auth_service.db.connect() as connection:
        connection.executemany(
            "INSERT INTO auth_attempts(username_key, ip_key, attempted_at) VALUES (?, ?, ?)",
            [("old-user", "old-ip", 1.0)] * 1500,
        )
    monkeypatch.setattr(auth_module.time, "time", lambda: 2000.0)
    assert_error(401, auth_service.login, "invalid", "short", IP)
    with auth_service.db.connect() as connection:
        assert (
            connection.execute("SELECT count(*) FROM auth_attempts").fetchone()[0]
            == 501
        )
