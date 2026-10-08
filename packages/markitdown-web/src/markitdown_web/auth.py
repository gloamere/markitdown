"""Invitation-only accounts with hashed credentials and revocable sessions.

The HTTP layer owns cookies, CSRF enforcement, and trusted client-IP selection.
This service deliberately repeats authorization checks at the database boundary.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import time
import uuid
from typing import TYPE_CHECKING, Any

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

from .governance import GovernanceService

if TYPE_CHECKING:
    from .state import Database, Settings


_USERNAME = re.compile(r"[a-z0-9_.-]{3,32}\Z", re.ASCII)
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z", re.ASCII)
_WINDOW_SECONDS = 15 * 60
_ACCOUNT_ATTEMPTS = 10
_IP_ATTEMPTS = 30
_CLEANUP_BATCH = 1000
_MAX_SESSIONS_PER_USER = 20
_MIB = 1024 * 1024
_LOGIN_ERROR = "用户名或密码不正确"
_INVITE_ERROR = "邀请码无效，请检查后重试"


class AuthError(Exception):
    """A safe error that the API can expose without internal exception details."""

    def __init__(self, status_code: int, detail: str, code: str | None = None) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.code = code


class AuthService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._passwords = PasswordHasher(
            time_cost=2,
            memory_cost=19456,
            parallelism=1,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )
        # Unknown users take the same expensive verification path as known users.
        # The throwaway password and hash never represent an actual account.
        self._dummy_hash = self._passwords.hash(secrets.token_urlsafe(32))
        with self.db.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1)),
                    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
                    daily_quota INTEGER NOT NULL CHECK (daily_quota BETWEEN 1 AND 1000),
                    max_file_bytes INTEGER NOT NULL
                        CHECK (max_file_bytes BETWEEN 1048576 AND 20971520),
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS invites (
                    id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_by TEXT NOT NULL REFERENCES users(id),
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    used_at REAL,
                    used_by TEXT REFERENCES users(id),
                    revoked_at REAL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
                CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
                CREATE TABLE IF NOT EXISTS auth_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username_key TEXT NOT NULL,
                    ip_key TEXT NOT NULL,
                    attempted_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS auth_attempts_user_time
                    ON auth_attempts(username_key, attempted_at);
                CREATE INDEX IF NOT EXISTS auth_attempts_ip_time
                    ON auth_attempts(ip_key, attempted_at);
                CREATE INDEX IF NOT EXISTS auth_attempts_time
                    ON auth_attempts(attempted_at);
                """
            )
        self.governance = GovernanceService(db, settings)

    @staticmethod
    def _public_user(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "username": row["username"],
            "is_admin": bool(row["is_admin"]),
            "is_active": bool(row["is_active"]),
            "daily_quota": row["daily_quota"],
            "max_file_bytes": row["max_file_bytes"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _normalized_username(username: str) -> str | None:
        # Bound input before normalization or hashing, including service callers
        # that bypass the HTTP layer's request size/type checks.
        if (
            not isinstance(username, str)
            or len(username) > 128
            or not username.isascii()
        ):
            return None
        normalized = username.strip().lower()
        if _USERNAME.fullmatch(normalized) is None:
            return None
        return normalized

    @staticmethod
    def _valid_password(password: str) -> bool:
        if not isinstance(password, str) or not 6 <= len(password) <= 128:
            return False
        try:
            return len(password.encode("utf-8")) <= 512
        except UnicodeEncodeError:
            return False

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    @staticmethod
    def _valid_token(token: str) -> bool:
        return isinstance(token, str) and _TOKEN.fullmatch(token) is not None

    @staticmethod
    def _valid_id(value: str) -> bool:
        return isinstance(value, str) and 0 < len(value) <= 128

    @staticmethod
    def _check_limits(daily_quota: int | None, max_file_bytes: int | None) -> None:
        if daily_quota is not None and (
            type(daily_quota) is not int or not 1 <= daily_quota <= 1000
        ):
            raise AuthError(400, "每日配额须为 1–1000 的整数")
        if max_file_bytes is not None and (
            type(max_file_bytes) is not int or not _MIB <= max_file_bytes <= 20 * _MIB
        ):
            raise AuthError(400, "单文件大小须为 1–20 MiB")

    def _start_attempt(self, username: str | None, ip: str) -> int:
        # Hash bounded keys so request-controlled data and raw IPs are not stored.
        username_key = hashlib.sha256(
            (username or "<invalid>").encode("ascii")
        ).hexdigest()
        bounded_ip = ip if isinstance(ip, str) and len(ip) <= 128 else "<unknown>"
        ip_key = hashlib.sha256(
            bounded_ip.encode("utf-8", errors="replace")
        ).hexdigest()
        now = time.time()
        cutoff = now - _WINDOW_SECONDS
        with self.db.transaction() as connection:
            connection.execute(
                "DELETE FROM auth_attempts WHERE id IN "
                "(SELECT id FROM auth_attempts WHERE attempted_at <= ? LIMIT ?)",
                (cutoff, _CLEANUP_BATCH),
            )
            account_count = connection.execute(
                "SELECT count(*) FROM auth_attempts "
                "WHERE username_key = ? AND attempted_at > ?",
                (username_key, cutoff),
            ).fetchone()[0]
            ip_count = connection.execute(
                "SELECT count(*) FROM auth_attempts "
                "WHERE ip_key = ? AND attempted_at > ?",
                (ip_key, cutoff),
            ).fetchone()[0]
            if account_count >= _ACCOUNT_ATTEMPTS or ip_count >= _IP_ATTEMPTS:
                raise AuthError(429, "尝试次数过多，请在 15 分钟后重试")
            # In-flight checks count too: concurrent requests cannot all pass
            # a read-only check before their failures are recorded.
            cursor = connection.execute(
                "INSERT INTO auth_attempts(username_key, ip_key, attempted_at) "
                "VALUES (?, ?, ?)",
                (username_key, ip_key, now),
            )
            assert cursor.lastrowid is not None
            return cursor.lastrowid

    def _verify_password(self, password_hash: str, password: str) -> bool:
        try:
            return self._passwords.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    def _require_admin(
        self, connection: sqlite3.Connection, admin_user_id: str
    ) -> sqlite3.Row:
        row = None
        if self._valid_id(admin_user_id):
            row = connection.execute(
                "SELECT * FROM users WHERE id = ?", (admin_user_id,)
            ).fetchone()
        if row is None or not row["is_admin"] or not row["is_active"]:
            raise AuthError(403, "需要有效的管理员账户")
        return row

    def has_admin(self) -> bool:
        """An existing disabled admin must not reopen first-run bootstrap."""
        with self.db.connect() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM users WHERE is_admin = 1 LIMIT 1"
                ).fetchone()
                is not None
            )

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        if not self._valid_id(user_id):
            return None
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            return None if row is None else self._public_user(row)

    def bootstrap_admin(
        self, username: str, password: str, *, request_id: str | None = None
    ) -> dict[str, Any]:
        normalized = self._normalized_username(username)
        if normalized is None:
            raise AuthError(400, "用户名须为 3–32 位英文字母、数字、下划线、点或连字符")
        if not self._valid_password(password):
            raise AuthError(400, "密码须为 6–128 个字符，且不超过 512 UTF-8 字节")
        if self.has_admin():
            raise AuthError(409, "管理员已初始化")
        password_hash = self._passwords.hash(password)
        user_id = uuid.uuid4().hex
        try:
            with self.governance.mutation(
                "local-operator",
                "account.bootstrap",
                "user",
                user_id,
                request_id=request_id,
            ) as (connection, audit):
                # The final check and insert are serialized across processes.
                if connection.execute(
                    "SELECT 1 FROM users WHERE is_admin = 1 LIMIT 1"
                ).fetchone():
                    raise AuthError(409, "管理员已初始化")
                defaults = self.governance.current(connection)
                connection.execute(
                    "INSERT INTO users(id, username, password_hash, is_admin, "
                    "is_active, daily_quota, max_file_bytes, created_at) "
                    "VALUES (?, ?, ?, 1, 1, ?, ?, ?)",
                    (
                        user_id,
                        normalized,
                        password_hash,
                        defaults["default_daily_quota"],
                        max(_MIB, defaults["default_max_file_bytes"]),
                        time.time(),
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM users WHERE id = ?", (user_id,)
                ).fetchone()
                audit.after = self._public_user(row)
                return self._public_user(row)
        except sqlite3.IntegrityError:
            raise AuthError(400, "无法使用该用户名") from None

    @staticmethod
    def _invite_status(invite: sqlite3.Row, now: float) -> str:
        if invite["used_at"] is not None:
            return "used"
        if invite["revoked_at"] is not None:
            return "revoked"
        if invite["expires_at"] <= now:
            return "expired"
        return "active"

    def _require_invite(
        self, connection: sqlite3.Connection, token_hash: str, now: float
    ) -> sqlite3.Row:
        invite = connection.execute(
            "SELECT * FROM invites WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if invite is None:
            raise AuthError(400, _INVITE_ERROR, "invite_invalid")
        status = self._invite_status(invite, now)
        messages = {
            "used": "邀请码已使用，请联系管理员获取新的邀请码",
            "revoked": "邀请码已撤销，请联系管理员获取新的邀请码",
            "expired": "邀请码已过期，请联系管理员获取新的邀请码",
        }
        if status != "active":
            raise AuthError(400, messages[status], f"invite_{status}")
        return invite

    def register(
        self,
        username: str,
        password: str,
        invite_token: str,
        ip: str,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        normalized = self._normalized_username(username)
        attempt_id = self._start_attempt(normalized, ip)
        if normalized is None:
            raise AuthError(400, "用户名须为 3–32 位英文字母、数字、下划线、点或连字符")
        if not self._valid_password(password):
            raise AuthError(400, "密码须为 6–128 个字符，且不超过 512 UTF-8 字节")
        if not self._valid_token(invite_token):
            raise AuthError(400, _INVITE_ERROR, "invite_invalid")
        token_hash = self._token_hash(invite_token)
        with self.db.connect() as connection:
            self._require_invite(connection, token_hash, time.time())
        password_hash = self._passwords.hash(password)
        user_id = uuid.uuid4().hex
        try:
            with self.governance.mutation(
                user_id,
                "account.register",
                "user",
                user_id,
                request_id=request_id,
            ) as (connection, audit):
                now = time.time()
                # Recheck after hashing and acquire the invite in the same
                # transaction as user creation. Duplicate usernames roll it back.
                invite = self._require_invite(connection, token_hash, now)
                defaults = self.governance.current(connection)
                connection.execute(
                    "INSERT INTO users(id, username, password_hash, is_admin, "
                    "is_active, daily_quota, max_file_bytes, created_at) "
                    "VALUES (?, ?, ?, 0, 1, ?, ?, ?)",
                    (
                        user_id,
                        normalized,
                        password_hash,
                        defaults["default_daily_quota"],
                        max(_MIB, defaults["default_max_file_bytes"]),
                        now,
                    ),
                )
                connection.execute(
                    "UPDATE invites SET used_at = ?, used_by = ? WHERE id = ?",
                    (now, user_id, invite["id"]),
                )
                connection.execute(
                    "DELETE FROM auth_attempts WHERE id = ?", (attempt_id,)
                )
                row = connection.execute(
                    "SELECT * FROM users WHERE id = ?", (user_id,)
                ).fetchone()
                audit.after = self._public_user(row)
                return self._public_user(row)
        except sqlite3.IntegrityError:
            raise AuthError(400, "无法使用该用户名") from None

    @staticmethod
    def _session_data(user: sqlite3.Row, raw_token: str) -> dict[str, Any]:
        return {
            "user": AuthService._public_user(user),
            "csrf_token": hashlib.sha256(
                ("csrf:" + raw_token).encode("ascii")
            ).hexdigest(),
        }

    def login(
        self, username: str, password: str, ip: str
    ) -> tuple[str, dict[str, Any]]:
        normalized = self._normalized_username(username)
        attempt_id = self._start_attempt(normalized, ip)
        if normalized is None or not self._valid_password(password):
            raise AuthError(401, _LOGIN_ERROR)
        with self.db.connect() as connection:
            user = connection.execute(
                "SELECT * FROM users WHERE username = ?", (normalized,)
            ).fetchone()
        password_hash = (
            user["password_hash"]
            if user is not None and user["is_active"]
            else self._dummy_hash
        )
        verified = self._verify_password(password_hash, password)
        if not verified or user is None or not user["is_active"]:
            raise AuthError(401, _LOGIN_ERROR)
        raw_token = secrets.token_urlsafe(32)
        with self.db.transaction() as connection:
            current = connection.execute(
                "SELECT * FROM users WHERE id = ?", (user["id"],)
            ).fetchone()
            # A concurrent disable must not be followed by creating a session.
            if (
                current is None
                or not current["is_active"]
                or current["password_hash"] != user["password_hash"]
            ):
                raise AuthError(401, _LOGIN_ERROR)
            now = time.time()
            connection.execute(
                "DELETE FROM sessions WHERE token_hash IN "
                "(SELECT token_hash FROM sessions WHERE expires_at <= ? LIMIT ?)",
                (now, _CLEANUP_BATCH),
            )
            connection.execute(
                "INSERT INTO sessions(token_hash, user_id, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    self._token_hash(raw_token),
                    current["id"],
                    now,
                    now + self.settings.session_seconds,
                ),
            )
            # Correct passwords do not count against the failure throttle.
            # Bound those successful sessions too, within this same transaction.
            # rowid breaks timestamp ties so the session just created is retained.
            connection.execute(
                "DELETE FROM sessions WHERE token_hash IN "
                "(SELECT token_hash FROM sessions WHERE user_id = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET ?)",
                (current["id"], _MAX_SESSIONS_PER_USER),
            )
            connection.execute("DELETE FROM auth_attempts WHERE id = ?", (attempt_id,))
            return raw_token, self._session_data(current, raw_token)

    def get_session(self, raw_token: str) -> dict[str, Any] | None:
        if not self._valid_token(raw_token):
            return None
        token_hash = self._token_hash(raw_token)
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT users.*, sessions.expires_at AS session_expires_at "
                "FROM sessions JOIN users ON users.id = sessions.user_id "
                "WHERE sessions.token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            if row["session_expires_at"] <= time.time() or not row["is_active"]:
                connection.execute(
                    "DELETE FROM sessions WHERE token_hash = ?", (token_hash,)
                )
                return None
            # Expiry is absolute: reading a session never extends it.
            return self._session_data(row, raw_token)

    def logout(self, raw_token: str) -> None:
        if not self._valid_token(raw_token):
            return
        with self.db.connect() as connection:
            connection.execute(
                "DELETE FROM sessions WHERE token_hash = ?",
                (self._token_hash(raw_token),),
            )

    def create_invite(
        self,
        admin_user_id: str,
        ttl_seconds: int | None = None,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        ttl = self.settings.invite_seconds if ttl_seconds is None else ttl_seconds
        raw_token = secrets.token_urlsafe(32)
        invite_id = uuid.uuid4().hex
        with self.governance.mutation(
            admin_user_id,
            "invite.create",
            "invite",
            invite_id,
            request_id=request_id,
        ) as (connection, audit):
            self._require_admin(connection, admin_user_id)
            if type(ttl) is not int or not 1 <= ttl <= 7 * 24 * 60 * 60:
                raise AuthError(400, "邀请码有效期须为 1 秒至 7 天")
            now = time.time()
            expires_at = now + ttl
            connection.execute(
                "INSERT INTO invites(id, token_hash, created_by, created_at, "
                "expires_at) VALUES (?, ?, ?, ?, ?)",
                (
                    invite_id,
                    self._token_hash(raw_token),
                    admin_user_id,
                    now,
                    expires_at,
                ),
            )
            audit.after = {
                "status": "active",
                "created_at": now,
                "expires_at": expires_at,
            }
        # The raw invite is intentionally available only at creation time.
        return {"id": invite_id, "token": raw_token, "expires_at": expires_at}

    def list_invites(self, admin_user_id: str) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            self._require_admin(connection, admin_user_id)
            rows = connection.execute(
                "SELECT id, created_by, created_at, expires_at, used_at, "
                "used_by, revoked_at FROM invites ORDER BY created_at DESC, id"
            ).fetchall()
        now = time.time()
        result = []
        for row in rows:
            invite = dict(row)
            invite["status"] = self._invite_status(row, now)
            result.append(invite)
        return result

    def revoke_invite(
        self, admin_user_id: str, invite_id: str, *, request_id: str | None = None
    ) -> None:
        with self.governance.mutation(
            admin_user_id,
            "invite.revoke",
            "invite",
            invite_id,
            request_id=request_id,
        ) as (connection, audit):
            self._require_admin(connection, admin_user_id)
            if not self._valid_id(invite_id):
                raise AuthError(404, "未找到邀请码")
            invite = connection.execute(
                "SELECT * FROM invites WHERE id = ?", (invite_id,)
            ).fetchone()
            if invite is None:
                raise AuthError(404, "未找到邀请码")
            now = time.time()
            audit.before = {
                "status": self._invite_status(invite, now),
                "revoked_at": invite["revoked_at"],
            }
            # Used, expired, and previously revoked invitations are already
            # unusable. Preserve that state instead of relabeling old events.
            if audit.before["status"] == "active":
                connection.execute(
                    "UPDATE invites SET revoked_at = ? WHERE id = ?", (now, invite_id)
                )
                audit.after = {"status": "revoked", "revoked_at": now}
            else:
                audit.after = dict(audit.before)

    def list_users(self, admin_user_id: str) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            self._require_admin(connection, admin_user_id)
            return [
                self._public_user(row)
                for row in connection.execute(
                    "SELECT * FROM users ORDER BY created_at, id"
                ).fetchall()
            ]

    def update_user(
        self,
        admin_user_id: str,
        target_id: str,
        *,
        daily_quota: int | None = None,
        max_file_bytes: int | None = None,
        is_active: bool | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        with self.governance.mutation(
            admin_user_id,
            "user.update",
            "user",
            target_id,
            request_id=request_id,
        ) as (connection, audit):
            self._require_admin(connection, admin_user_id)
            self._check_limits(daily_quota, max_file_bytes)
            if (
                max_file_bytes is not None
                and max_file_bytes > self.settings.max_file_bytes
            ):
                raise AuthError(400, "单文件大小不能超过部署上限")
            if is_active is not None and type(is_active) is not bool:
                raise AuthError(400, "账户状态须为布尔值")
            target = None
            if self._valid_id(target_id):
                target = connection.execute(
                    "SELECT * FROM users WHERE id = ?", (target_id,)
                ).fetchone()
            if target is None:
                raise AuthError(404, "未找到账户")
            audit.before = self._public_user(target)
            if is_active is False:
                if target_id == admin_user_id:
                    raise AuthError(400, "不能停用自己的管理员账户")
                if target["is_admin"] and target["is_active"]:
                    active_admins = connection.execute(
                        "SELECT count(*) FROM users "
                        "WHERE is_admin = 1 AND is_active = 1"
                    ).fetchone()[0]
                    if active_admins <= 1:
                        raise AuthError(400, "不能停用最后一个有效管理员账户")
            connection.execute(
                "UPDATE users SET daily_quota = ?, max_file_bytes = ?, "
                "is_active = ? WHERE id = ?",
                (
                    target["daily_quota"] if daily_quota is None else daily_quota,
                    target["max_file_bytes"]
                    if max_file_bytes is None
                    else max_file_bytes,
                    target["is_active"] if is_active is None else int(is_active),
                    target_id,
                ),
            )
            if is_active is False:
                removed = connection.execute(
                    "DELETE FROM sessions WHERE user_id = ?", (target_id,)
                ).rowcount
            else:
                removed = 0
            if is_active is not None:
                self.governance.record_account_state(connection, target_id, is_active)
            row = connection.execute(
                "SELECT * FROM users WHERE id = ?", (target_id,)
            ).fetchone()
            audit.after = {**self._public_user(row), "sessions_revoked": removed}
            return self._public_user(row)

    def reset_password(
        self, username: str, password: str, *, request_id: str | None = None
    ) -> dict[str, Any]:
        """Local-operator recovery only. Never expose this method as a web route.

        The CLI must obtain the new password using an interactive, hidden prompt.
        Existing accounts and disabled status are preserved; all sessions expire.
        """
        normalized = self._normalized_username(username)
        with self.governance.mutation(
            "local-operator", "account.recover", "user", request_id=request_id
        ) as (connection, audit):
            if normalized is None:
                raise AuthError(400, "用户名须为 3–32 位英文字母、数字、下划线、点或连字符")
            target = connection.execute(
                "SELECT * FROM users WHERE username = ?", (normalized,)
            ).fetchone()
            if target is None:
                raise AuthError(404, "未找到账户")
            audit.target_id = target["id"]
            audit.before = {"is_active": bool(target["is_active"])}
            if not self._valid_password(password):
                raise AuthError(400, "密码须为 6–128 个字符，且不超过 512 UTF-8 字节")
            password_hash = self._passwords.hash(password)
            connection.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (password_hash, target["id"]),
            )
            removed = connection.execute(
                "DELETE FROM sessions WHERE user_id = ?", (target["id"],)
            ).rowcount
            audit.after = {
                "is_active": bool(target["is_active"]),
                "password_changed": True,
                "sessions_revoked": removed,
            }
            return self._public_user(target)
