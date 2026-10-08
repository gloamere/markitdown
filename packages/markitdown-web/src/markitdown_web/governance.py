"""Versioned business defaults and bounded, content-free administrator audit.

Deployment security settings are deliberately read-only. Business changes apply
only to newly created accounts/jobs; callers snapshot ``current()`` at admission.
Audit rows are committed with successful mutations, or separately after rollback
for rejected mutations. No request bodies, credentials, paths, or documents are
accepted as audit values.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    from .state import Database, Settings

_MIB = 1024 * 1024
_FIELDS = ("default_daily_quota", "default_max_file_bytes", "retention_seconds")
_ACTIONS = {
    "account.bootstrap",
    "account.register",
    "account.recover",
    "invite.create",
    "invite.revoke",
    "user.update",
    "settings.update",
}
_AUDIT_NUMBERS = {
    *_FIELDS,
    "daily_quota",
    "max_file_bytes",
    "is_admin",
    "is_active",
    "expires_at",
    "created_at",
    "used_at",
    "revoked_at",
    "version",
    "sessions_revoked",
    "password_changed",
}


class GovernanceError(Exception):
    """A safe, structured error suitable for the HTTP boundary."""

    def __init__(self, status_code: int, detail: str, code: str | None = None) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.code = code


def _identifier(value: Any) -> str | None:
    """Only generated UUID identifiers, never raw user-controlled strings."""
    if not isinstance(value, str) or len(value) not in (32, 36):
        return None
    try:
        return uuid.UUID(value).hex
    except (ValueError, AttributeError):
        return None


def _safe_values(values: dict[str, Any]) -> str:
    result: dict[str, Any] = {}
    for key, value in values.items():
        if key in _AUDIT_NUMBERS and (
            value is None or type(value) in (int, float, bool)
        ):
            result[key] = value
        elif (
            key == "status"
            and isinstance(value, str)
            and value in {"active", "used", "revoked", "expired"}
        ):
            result[key] = value
    return json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass
class AuditMutation:
    target_id: str | None = None
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)


class GovernanceService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        with self.db.transaction() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS business_settings ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), "
                "version INTEGER NOT NULL CHECK (version >= 1), "
                "default_daily_quota INTEGER NOT NULL "
                "CHECK (default_daily_quota BETWEEN 1 AND 1000), "
                "default_max_file_bytes INTEGER NOT NULL "
                "CHECK (default_max_file_bytes BETWEEN 1 AND 20971520), "
                "retention_seconds NUMERIC NOT NULL CHECK (retention_seconds > 0), "
                "updated_at REAL NOT NULL, updated_by TEXT)"
            )
            connection.execute(
                "INSERT INTO business_settings VALUES (1, 1, ?, ?, ?, ?, NULL) "
                "ON CONFLICT(id) DO NOTHING",
                (
                    settings.default_daily_quota,
                    min(settings.default_max_file_bytes, settings.max_file_bytes),
                    settings.retention_seconds,
                    time.time(),
                ),
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS admin_audit ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL, "
                "actor_id TEXT, action TEXT NOT NULL, target_type TEXT NOT NULL, "
                "target_id TEXT, before_json TEXT NOT NULL, after_json TEXT NOT NULL, "
                "result TEXT NOT NULL CHECK (result IN ('success', 'rejected', 'error')), "
                "request_id TEXT NOT NULL, error_status INTEGER)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS admin_audit_target "
                "ON admin_audit(target_type, target_id, id)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS account_revocations ("
                "user_id TEXT PRIMARY KEY, disabled_at REAL, "
                "is_active INTEGER NOT NULL CHECK (is_active IN (0, 1)), "
                "updated_at REAL NOT NULL)"
            )
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
            ).fetchone():
                # Preserve known disabled state when upgrading an older database.
                # Historical disable timestamps were not recorded, so use the
                # migration time rather than inventing an earlier event time.
                now = time.time()
                connection.execute(
                    "INSERT OR IGNORE INTO account_revocations "
                    "SELECT id, ?, 0, ? FROM users WHERE is_active = 0",
                    (now, now),
                )

    @staticmethod
    def record_account_state(
        connection: sqlite3.Connection, user_id: str, is_active: bool
    ) -> None:
        """Keep the latest explicit state for filtering an older backup restore.

        Recovery must read this ledger from the current database before replacing
        it with a backup. A backup's own ledger cannot describe newer revocations.
        """
        now = time.time()
        connection.execute(
            "INSERT INTO account_revocations(user_id, disabled_at, is_active, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
            "disabled_at = CASE WHEN excluded.is_active = 0 THEN excluded.disabled_at "
            "ELSE account_revocations.disabled_at END, "
            "is_active = excluded.is_active, updated_at = excluded.updated_at",
            (user_id, None if is_active else now, int(is_active), now),
        )

    @staticmethod
    def _require_admin(connection: sqlite3.Connection, actor_id: str) -> None:
        row = connection.execute(
            "SELECT is_admin, is_active FROM users WHERE id = ?",
            (_identifier(actor_id),),
        ).fetchone()
        if row is None or not row["is_admin"] or not row["is_active"]:
            raise GovernanceError(403, "需要有效的管理员账户", "admin_required")

    def _record(
        self,
        connection: sqlite3.Connection,
        *,
        actor_id: str | None,
        action: str,
        target_type: str,
        mutation: AuditMutation,
        request_id: str,
        result: str,
        error_status: int | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO admin_audit(created_at, actor_id, action, target_type, "
            "target_id, before_json, after_json, result, request_id, error_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                time.time(),
                "local-operator"
                if actor_id == "local-operator"
                else _identifier(actor_id),
                action,
                target_type,
                "global"
                if target_type == "settings"
                else _identifier(mutation.target_id),
                _safe_values(mutation.before),
                _safe_values(mutation.after),
                result,
                request_id,
                error_status,
            ),
        )

    @contextmanager
    def mutation(
        self,
        actor_id: str | None,
        action: str,
        target_type: str,
        target_id: str | None = None,
        *,
        request_id: str | None = None,
    ) -> Iterator[tuple[sqlite3.Connection, AuditMutation]]:
        """Internal transaction helper; the caller still enforces authorization."""
        if action not in _ACTIONS or target_type not in {"user", "invite", "settings"}:
            raise ValueError("Unsupported audit operation")
        audit = AuditMutation(target_id=target_id)
        correlation = _identifier(request_id) or uuid.uuid4().hex
        try:
            with self.db.transaction() as connection:
                yield connection, audit
                self._record(
                    connection,
                    actor_id=actor_id,
                    action=action,
                    target_type=target_type,
                    mutation=audit,
                    request_id=correlation,
                    result="success",
                )
        except Exception as error:
            # The business transaction has rolled back before recording failure.
            # Log only a numeric status; exception text can contain private data.
            status = getattr(error, "status_code", None)
            status = status if type(status) is int and 400 <= status <= 599 else None
            with self.db.transaction() as connection:
                self._record(
                    connection,
                    actor_id=actor_id,
                    action=action,
                    target_type=target_type,
                    mutation=audit,
                    request_id=correlation,
                    result="rejected" if status is not None else "error",
                    error_status=status,
                )
            raise

    @staticmethod
    def _row(connection: sqlite3.Connection) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM business_settings WHERE id = 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("Business settings are not initialized")
        return row

    def current(self, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
        """Internal effective snapshot; use an existing transaction when available."""
        if connection is None:
            with self.db.connect() as owned_connection:
                return self.current(owned_connection)
        row = self._row(connection)
        return {
            "version": row["version"],
            "default_daily_quota": row["default_daily_quota"],
            "default_max_file_bytes": min(
                row["default_max_file_bytes"], self.settings.max_file_bytes
            ),
            "retention_seconds": self._values(row)["retention_seconds"],
        }

    @staticmethod
    def _values(row: sqlite3.Row) -> dict[str, Any]:
        # Integral deployment values round-trip through the integer-only Web
        # mutation API. Fractional trusted deployment TTLs remain unchanged.
        return {
            key: int(row[key])
            if isinstance(row[key], float) and row[key].is_integer()
            else row[key]
            for key in _FIELDS
        }

    def _settings_view(self, connection: sqlite3.Connection) -> dict[str, Any]:
        row = self._row(connection)
        current = self._values(row)
        effective = self.current(connection)
        effective.pop("version")
        return {
            "version": row["version"],
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
            "defaults": {key: getattr(self.settings, key) for key in _FIELDS},
            "current": current,
            "effective": effective,
            "bounds": {
                "default_daily_quota": {"min": 1, "max": 1000},
                "default_max_file_bytes": {
                    "min": _MIB,
                    "max": min(20 * _MIB, self.settings.max_file_bytes),
                },
                "retention_seconds": {"min": 3600, "max": 7 * 24 * 3600},
            },
            "deployment": {
                # Explicit allowlist: never expose host paths, secrets, environment,
                # arbitrary endpoint URLs, or configurable security bypasses.
                key: getattr(self.settings, key)
                for key in (
                    "session_seconds",
                    "invite_seconds",
                    "cookie_secure",
                    "global_concurrency",
                    "per_user_concurrency",
                    "history_seconds",
                    "cleanup_interval",
                    "max_storage_bytes",
                    "max_queue_jobs",
                    "max_files",
                    "max_total_bytes",
                    "max_file_bytes",
                    "max_attempts",
                    "docling_enabled",
                )
            },
            "applies_to": {
                "default_daily_quota": "new_accounts",
                "default_max_file_bytes": "new_accounts",
                "retention_seconds": "new_jobs",
            },
        }

    def get_settings(self, admin_id: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            self._require_admin(connection, admin_id)
            return self._settings_view(connection)

    def update_settings(
        self,
        admin_id: str,
        changes: dict[str, Any],
        *,
        expected_version: int,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        with self.mutation(
            admin_id, "settings.update", "settings", request_id=request_id
        ) as (connection, audit):
            self._require_admin(connection, admin_id)
            before = self._row(connection)
            audit.before = {key: before[key] for key in (*_FIELDS, "version")}
            if type(expected_version) is not int or expected_version < 1:
                raise GovernanceError(400, "请提供有效的配置版本", "invalid_version")
            if expected_version != before["version"]:
                raise GovernanceError(409, "配置已被其他操作修改，请刷新后重试", "version_conflict")
            if (
                not isinstance(changes, dict)
                or not changes
                or set(changes) - set(_FIELDS)
            ):
                raise GovernanceError(400, "只允许修改业务默认额度、文件大小和保留时间", "invalid_settings")
            bounds = self._settings_view(connection)["bounds"]
            for key, value in changes.items():
                if (
                    type(value) is not int
                    or not bounds[key]["min"] <= value <= bounds[key]["max"]
                ):
                    raise GovernanceError(400, "配置值超出允许范围，且须为整数", "invalid_settings")
            values = {key: changes.get(key, before[key]) for key in _FIELDS}
            # An identical save succeeds without changing the version or timestamp.
            if values != {key: before[key] for key in _FIELDS}:
                connection.execute(
                    "UPDATE business_settings SET version = version + 1, "
                    "default_daily_quota = ?, default_max_file_bytes = ?, "
                    "retention_seconds = ?, updated_at = ?, updated_by = ? "
                    "WHERE id = 1 AND version = ?",
                    (
                        *(values[key] for key in _FIELDS),
                        time.time(),
                        admin_id,
                        expected_version,
                    ),
                )
            after = self._row(connection)
            audit.after = {key: after[key] for key in (*_FIELDS, "version")}
            return self._settings_view(connection)

    def list_audit(
        self, admin_id: str, *, limit: int = 100, before_id: int | None = None
    ) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            self._require_admin(connection, admin_id)
            if type(limit) is not int or not 1 <= limit <= 200:
                raise GovernanceError(400, "审计查询数量须为 1–200", "invalid_page")
            if before_id is not None and (type(before_id) is not int or before_id < 1):
                raise GovernanceError(400, "无效的审计分页位置", "invalid_page")
            rows = connection.execute(
                "SELECT * FROM admin_audit WHERE (? IS NULL OR id < ?) "
                "ORDER BY id DESC LIMIT ?",
                (before_id, before_id, limit),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["before"] = json.loads(item.pop("before_json"))
                item["after"] = json.loads(item.pop("after_json"))
                result.append(item)
            return result
