"""Offline, private snapshots and independently current recovery journals.

These are operator-only filesystem APIs. No network or credentials are logged.
A checksum detects damage, not a malicious operator: retain both artifacts in
trusted private storage. Freshness is an explicit operator assertion in addition
to the mechanically checked lineage and snapshot/export timestamps.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import time
import uuid
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Iterator

from filelock import FileLock, Timeout

from .conversion import MAX_HTML_BYTES, MAX_MARKDOWN_BYTES

SCHEMA_VERSION = 1
MAX_METADATA_BYTES = 16 * 1024 * 1024
MAX_DATABASE_BYTES = 256 * 1024 * 1024
MAX_BACKUP_BYTES = 4 * 1024 * 1024 * 1024
MAX_FILES = 100_000
MAX_ROWS = 100_000
_ID = re.compile(r"[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_SUFFIXES = {".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv", ".json"}
_DB_NAME = "workspace.sqlite3"
_MANIFEST = "manifest.json"
_REQUIRED_TABLES = {
    "users",
    "sessions",
    "invites",
    "jobs",
    "daily_usage",
    "job_tombstones",
    "account_revocations",
    "admin_audit",
    "job_attempts",
    "job_retry_requests",
    "job_submissions",
}


class RecoveryError(Exception):
    """A bounded, content-free operator error; never print raw SQLite errors."""


def _require(condition: Any, message: str) -> None:
    if not condition:
        raise RecoveryError(message)


def _require_private_permissions() -> None:
    _require(
        os.name == "posix",
        "Offline recovery requires POSIX private permissions; Windows ACL-aware recovery is not supported",
    )


def _path(raw: Path) -> Path:
    path = Path(raw).expanduser().absolute()
    _require(".." not in path.parts, "Recovery paths must not contain parent traversal")
    _require(
        not any(p.is_symlink() for p in (path, *path.parents)),
        "Recovery paths must not contain symbolic links",
    )
    return path


def _private(path: Path, *, directory: bool = False) -> os.stat_result:
    info = path.lstat()
    _require(
        stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
        "Recovery input must be an ordinary directory or regular file",
    )
    _require(
        directory or info.st_nlink == 1, "Hard-linked recovery files are not supported"
    )
    if os.name == "posix":
        _require(
            info.st_uid == os.geteuid() and not (info.st_mode & 0o077),
            "Recovery inputs must be owned by the operator and private (0700/0600)",
        )
    return info


def _new_directory(path: Path, *, allow_empty: bool = False) -> None:
    _path(path)
    _require(path.parent.is_dir(), "Recovery destination parent must already exist")
    if path.exists():
        _require(allow_empty, "Recovery destination already exists")
        _private(path, directory=True)
        _require(not any(path.iterdir()), "Restore destination must be empty")
    else:
        path.mkdir(mode=0o700)
    path.chmod(0o700)


def _separate(first: Path, second: Path) -> None:
    _require(
        first != second and first not in second.parents and second not in first.parents,
        "Recovery source and destination must be separate trees",
    )


def _write(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def _read(path: Path, maximum: int) -> bytes:
    _path(path)
    _private(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        _require(
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and info.st_size <= maximum,
            "Recovery input exceeds its size bound or is not a regular file",
        )
        content = source.read(maximum + 1)
    _require(len(content) <= maximum, "Recovery input exceeds its size bound")
    return content


def _json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, "Recovery metadata contains duplicate keys")
        result[key] = value
    return result


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            _read(path, MAX_METADATA_BYTES), object_pairs_hook=_no_duplicate_keys
        )
    except (ValueError, UnicodeError, RecursionError):
        raise RecoveryError("Recovery metadata is malformed") from None
    _require(type(value) is dict, "Recovery metadata must be an object")
    return value


def _fields(value: Any, names: set[str]) -> None:
    _require(
        type(value) is dict and set(value) == names,
        "Unsupported recovery metadata fields",
    )


def _timestamp(value: Any) -> bool:
    return (
        type(value) in (int, float)
        and math.isfinite(value)
        and 0 <= value <= time.time() + 300
    )


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    # No immutable=1 for the live source: committed WAL data must be included.
    connection = sqlite3.connect(
        path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"), uri=True, timeout=0
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


def _database_check(connection: sqlite3.Connection) -> None:
    # The live main file may be small while committed WAL pages grow the logical
    # database. Bound logical size before integrity scans or a backup allocation.
    logical_bytes = (
        connection.execute("PRAGMA page_count").fetchone()[0]
        * connection.execute("PRAGMA page_size").fetchone()[0]
    )
    _require(logical_bytes <= MAX_DATABASE_BYTES, "Database exceeds backup size bound")
    objects = connection.execute("SELECT type,name FROM sqlite_master").fetchall()
    _require(
        not any(row["type"] in {"trigger", "view"} for row in objects),
        "Recovery databases must not contain triggers or views",
    )
    tables = {row["name"] for row in objects if row["type"] == "table"}
    _require(
        _REQUIRED_TABLES <= tables,
        "Recovery ledger schema is missing; upgrade before backup",
    )
    _require(
        connection.execute("PRAGMA quick_check").fetchall()[0][0] == "ok",
        "Recovery database failed integrity validation",
    )
    _require(
        connection.execute("PRAGMA foreign_key_check").fetchone() is None,
        "Recovery database has broken ownership references",
    )


def _rows(connection: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    rows = connection.execute(query).fetchmany(MAX_ROWS + 1)
    _require(len(rows) <= MAX_ROWS, "Recovery ledger exceeds supported row bounds")
    return [dict(row) for row in rows]


def _identity(connection: sqlite3.Connection) -> str:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS recovery_identity ("
        "id INTEGER PRIMARY KEY CHECK(id=1), source_id TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT OR IGNORE INTO recovery_identity VALUES (1, ?)", (uuid.uuid4().hex,)
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS recovery_password_resets ("
        "user_id TEXT PRIMARY KEY, reset_at REAL NOT NULL)"
    )
    source_id = connection.execute(
        "SELECT source_id FROM recovery_identity WHERE id=1"
    ).fetchone()[0]
    _require(_identifier(source_id), "Recovery source identity is invalid")
    return str(source_id)


@contextmanager
def _stopped(data_dir: Path) -> Iterator[tuple[Path, sqlite3.Connection, str]]:
    root = _path(data_dir)
    _private(root, directory=True)
    _require(
        not (root / _MANIFEST).exists(),
        "A snapshot cannot supply an independently current journal",
    )
    database = root / _DB_NAME
    _private(database)
    _require(
        database.stat().st_size <= MAX_DATABASE_BYTES,
        "Database exceeds backup size bound",
    )
    for sidecar in (root / (_DB_NAME + "-wal"), root / (_DB_NAME + "-shm")):
        if sidecar.exists() or sidecar.is_symlink():
            _private(sidecar)
    lock_path = _path(root / "worker.lock")
    if lock_path.exists():
        _private(lock_path)
    lock = FileLock(str(lock_path), timeout=0, mode=0o600)
    try:
        lock.acquire()
    except Timeout:
        raise RecoveryError(
            "Stop the service and all workers before recovery operations"
        ) from None
    connection = None
    try:
        _private(lock_path)
        connection = _connect(database)
        connection.execute("BEGIN IMMEDIATE")
        _database_check(connection)
        source_id = _identity(connection)
        # Persist lineage before a separate read connection takes the snapshot.
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        yield root, connection, source_id
    finally:
        if connection is not None:
            connection.rollback()
            connection.close()
        lock.release()


def _scan(root: Path) -> set[str]:
    """Bounded tree scan with no symlink/special-file traversal."""
    result: set[str] = set()
    pending = [root]
    count = 0
    while pending:
        directory = pending.pop()
        _private(directory, directory=True)
        for path in directory.iterdir():
            count += 1
            _require(count <= MAX_FILES * 2, "Recovery tree exceeds entry bounds")
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            else:
                _private(path)
                result.add(path.relative_to(root).as_posix())
    return result


def _file_bound(relative: str) -> int:
    if relative == _DB_NAME:
        return MAX_DATABASE_BYTES
    parts = relative.split("/")
    _require(
        len(parts) == 3 and parts[0] == "jobs" and _identifier(parts[1]),
        "Recovery manifest contains an unsafe path",
    )
    name = parts[2]
    if name == "markdown.md":
        return MAX_MARKDOWN_BYTES
    if name == "preview.html":
        return MAX_HTML_BYTES
    _require(
        name.startswith("source") and name[6:] in _SUFFIXES,
        "Recovery manifest contains an unsupported payload",
    )
    return 20 * 1024 * 1024


def _artifacts(
    connection: sqlite3.Connection, root: Path, snapshot_at: float
) -> list[str]:
    _scan(root / "jobs")
    files = []
    for row in _rows(
        connection, "SELECT id,suffix,status,expires_at FROM jobs ORDER BY id"
    ):
        _require(
            _identifier(row["id"]) and row["suffix"] in _SUFFIXES,
            "Database contains an unsafe job identifier or suffix",
        )
        if row["status"] == "expired" or row["expires_at"] <= snapshot_at:
            continue
        prefix = f"jobs/{row['id']}/"
        source = prefix + "source" + row["suffix"]
        if row["status"] != "failed" or (root / source).exists():
            files.append(source)
        if row["status"] == "succeeded":
            files.extend((prefix + "markdown.md", prefix + "preview.html"))
    _require(len(files) < MAX_FILES, "Backup has too many files")
    return files


def _check_sources(
    connection: sqlite3.Connection, records: list[dict[str, Any]]
) -> None:
    by_path = {record["path"]: record for record in records}
    for row in _rows(connection, "SELECT id,suffix,size,source_sha256 FROM jobs"):
        record = by_path.get(f"jobs/{row['id']}/source{row['suffix']}")
        if record is not None:
            _require(
                record["size"] == row["size"]
                and (
                    not row["source_sha256"] or record["sha256"] == row["source_sha256"]
                ),
                "Backup source does not match the recorded job submission",
            )


def _record(path: Path, relative: str) -> dict[str, Any]:
    content = _read(path, _file_bound(relative))
    return {
        "path": relative,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _sync_directory(path: Path) -> None:
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _sync_tree(root: Path) -> None:
    # File data is fsynced at write time; directory entries need their own sync.
    directories = [root / "jobs"]
    if directories[0].exists():
        directories.extend(path for path in directories[0].iterdir() if path.is_dir())
    for path in reversed(directories):
        if path.exists():
            _sync_directory(path)
    _sync_directory(root)
    _sync_directory(root.parent)


def create_backup(data_dir: Path, backup_dir: Path) -> dict[str, Any]:
    """Create a new private stopped-service snapshot; never overwrite a backup."""
    _require_private_permissions()
    destination = _path(backup_dir)
    _separate(_path(data_dir), destination)
    created = False
    complete = False
    try:
        with _stopped(data_dir) as (root, connection, source_id):
            _new_directory(destination)
            created = True
            (destination / "jobs").mkdir(mode=0o700)
            snapshot_at = time.time()
            files = _artifacts(connection, root, snapshot_at)
            _write(destination / _DB_NAME, b"")
            source = _connect(root / _DB_NAME, readonly=True)
            snapshot = _connect(destination / _DB_NAME)
            try:
                source.backup(snapshot)
                snapshot.execute("PRAGMA journal_mode=DELETE")
                _database_check(snapshot)
            finally:
                snapshot.close()
                source.close()
            records = [_record(destination / _DB_NAME, _DB_NAME)]
            total = records[0]["size"]
            for relative in files:
                content = _read(root / relative, _file_bound(relative))
                total += len(content)
                _require(total <= MAX_BACKUP_BYTES, "Backup exceeds total size bound")
                output = destination / relative
                output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                _write(output, content)
                records.append(
                    {
                        "path": relative,
                        "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                )
            _check_sources(connection, records)
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "snapshot_id": uuid.uuid4().hex,
                "source_id": source_id,
                "created_at": snapshot_at,
                "files": records,
            }
            encoded = _json(manifest)
            _require(
                len(encoded) <= MAX_METADATA_BYTES, "Backup manifest exceeds size bound"
            )
            _write(destination / _MANIFEST, encoded)
            _sync_tree(destination)
            complete = True
            return {
                "snapshot_id": manifest["snapshot_id"],
                "created_at": snapshot_at,
                "files": len(records),
                "bytes": total,
            }
    except (OSError, sqlite3.Error) as error:
        raise RecoveryError(
            "Backup failed; check private paths, stopped service, database and free space"
        ) from error
    finally:
        if created and not complete:
            shutil.rmtree(destination)


def export_recovery_journal(data_dir: Path, journal_path: Path) -> dict[str, Any]:
    """Export deletion/account/usage state from the independently CURRENT database."""
    _require_private_permissions()
    output = _path(journal_path)
    _separate(_path(data_dir), output)
    try:
        _require(
            output.parent.is_dir() and not output.exists(),
            "Journal destination must be a new file",
        )
        _private(output.parent, directory=True)
        with _stopped(data_dir) as (_, connection, source_id):
            exported_at = time.time()
            resets = {
                row["user_id"]: row["reset_at"]
                for row in _rows(
                    connection, "SELECT user_id,reset_at FROM recovery_password_resets"
                )
            }
            for row in _rows(
                connection,
                "SELECT target_id AS user_id,MAX(created_at) AS reset_at "
                "FROM admin_audit WHERE action='account.recover' AND result='success' "
                "GROUP BY target_id",
            ):
                resets[row["user_id"]] = max(
                    resets.get(row["user_id"], 0), row["reset_at"]
                )
            journal: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION,
                "source_id": source_id,
                "exported_at": exported_at,
                "job_tombstones": _rows(
                    connection,
                    "SELECT job_id,user_id,deleted_at,reason FROM job_tombstones ORDER BY job_id",
                ),
                "account_revocations": _rows(
                    connection,
                    "SELECT user_id,disabled_at,is_active,updated_at FROM account_revocations ORDER BY user_id",
                ),
                "accounts": _rows(
                    connection,
                    "SELECT id AS user_id,is_active,is_admin,daily_quota,max_file_bytes FROM users ORDER BY id",
                ),
                "daily_usage": _rows(
                    connection,
                    "SELECT user_id,day,used FROM daily_usage ORDER BY user_id,day",
                ),
                "password_resets": [
                    {"user_id": user_id, "reset_at": timestamp}
                    for user_id, timestamp in sorted(resets.items())
                ],
            }
            _validate_journal(journal)
            encoded = _json(
                {
                    "journal": journal,
                    "sha256": hashlib.sha256(_json(journal)).hexdigest(),
                }
            )
            _require(
                len(encoded) <= MAX_METADATA_BYTES,
                "Recovery journal exceeds size bound",
            )
            _write(output, encoded)
            _sync_directory(output.parent)
            return {
                "exported_at": exported_at,
                "tombstones": len(journal["job_tombstones"]),
                "accounts": len(journal["accounts"]),
            }
    except (OSError, sqlite3.Error) as error:
        raise RecoveryError(
            "Journal export failed; a current readable database is required"
        ) from error


def _validate_journal(journal: dict[str, Any]) -> None:
    _fields(
        journal,
        {
            "schema_version",
            "source_id",
            "exported_at",
            "job_tombstones",
            "account_revocations",
            "accounts",
            "daily_usage",
            "password_resets",
        },
    )
    _require(
        type(journal["schema_version"]) is int
        and journal["schema_version"] == SCHEMA_VERSION
        and _identifier(journal["source_id"])
        and _timestamp(journal["exported_at"]),
        "Recovery journal has an unsupported version, identity or timestamp",
    )
    definitions = {
        "job_tombstones": {"job_id", "user_id", "deleted_at", "reason"},
        "account_revocations": {"user_id", "disabled_at", "is_active", "updated_at"},
        "accounts": {
            "user_id",
            "is_active",
            "is_admin",
            "daily_quota",
            "max_file_bytes",
        },
        "daily_usage": {"user_id", "day", "used"},
        "password_resets": {"user_id", "reset_at"},
    }
    for name, fields in definitions.items():
        rows = journal[name]
        _require(
            type(rows) is list and len(rows) <= MAX_ROWS,
            "Recovery journal exceeds row bounds",
        )
        seen = set()
        for row in rows:
            _fields(row, fields)
            _require(
                _identifier(row["user_id"]),
                "Recovery journal has an invalid account identifier",
            )
            key = row.get("job_id", (row["user_id"], row.get("day")))
            _require(key not in seen, "Recovery journal has duplicate ledger records")
            seen.add(key)
            for field in ("deleted_at", "updated_at", "reset_at"):
                if field in row:
                    _require(
                        _timestamp(row[field]) and row[field] <= journal["exported_at"],
                        "Recovery journal has an invalid event timestamp",
                    )
            for field in ("is_active", "is_admin"):
                if field in row:
                    _require(
                        type(row[field]) is int and row[field] in (0, 1),
                        "Recovery journal has an invalid account state",
                    )
            if name == "job_tombstones":
                _require(
                    _identifier(row["job_id"])
                    and row["reason"]
                    in {"user_deleted", "history_expired", "restore_filtered"},
                    "Recovery journal has an invalid tombstone",
                )
            elif name == "account_revocations":
                _require(
                    row["disabled_at"] is None
                    or (
                        _timestamp(row["disabled_at"])
                        and row["disabled_at"] <= row["updated_at"]
                    ),
                    "Recovery journal has an invalid disable timestamp",
                )
                _require(
                    row["is_active"] == 1 or row["disabled_at"] is not None,
                    "Disabled account is missing its durable revocation",
                )
            elif name == "accounts":
                _require(
                    type(row["daily_quota"]) is int
                    and 1 <= row["daily_quota"] <= 1000
                    and type(row["max_file_bytes"]) is int
                    and 1_048_576 <= row["max_file_bytes"] <= 20 * 1024 * 1024,
                    "Recovery journal has invalid account limits",
                )
            elif name == "daily_usage":
                _require(
                    type(row["used"]) is int
                    and 0 <= row["used"] <= 2**63 - 1
                    and isinstance(row["day"], str)
                    and len(row["day"]) == 10,
                    "Recovery journal has invalid daily usage",
                )
                try:
                    _require(
                        date.fromisoformat(row["day"]).isoformat() == row["day"],
                        "Recovery journal has invalid usage day",
                    )
                except ValueError:
                    raise RecoveryError(
                        "Recovery journal has invalid usage day"
                    ) from None
    accounts = {row["user_id"]: row for row in journal["accounts"]}
    revocations = {row["user_id"]: row for row in journal["account_revocations"]}
    for user_id, account in accounts.items():
        revocation = revocations.get(user_id)
        _require(
            (revocation is None and account["is_active"] == 1)
            or (
                revocation is not None
                and revocation["is_active"] == account["is_active"]
            ),
            "Account state conflicts with its durable revocation ledger",
        )


def _validate_snapshot(root: Path) -> dict[str, Any]:
    _private(root, directory=True)
    manifest = _read_json(root / _MANIFEST)
    _fields(
        manifest, {"schema_version", "snapshot_id", "source_id", "created_at", "files"}
    )
    _require(
        type(manifest["schema_version"]) is int
        and manifest["schema_version"] == SCHEMA_VERSION
        and _identifier(manifest["snapshot_id"])
        and _identifier(manifest["source_id"])
        and _timestamp(manifest["created_at"]),
        "Backup manifest version, identity or timestamp is invalid",
    )
    _require(
        type(manifest["files"]) is list and 1 <= len(manifest["files"]) <= MAX_FILES,
        "Backup manifest exceeds file bounds",
    )
    paths = set()
    total = 0
    for record in manifest["files"]:
        _fields(record, {"path", "size", "sha256"})
        relative = record["path"]
        _require(
            isinstance(relative, str) and len(relative) <= 128,
            "Backup manifest path is invalid",
        )
        maximum = _file_bound(relative)
        _require(
            relative not in paths
            and type(record["size"]) is int
            and 0 <= record["size"] <= maximum
            and isinstance(record["sha256"], str)
            and _HASH.fullmatch(record["sha256"]),
            "Backup manifest record is invalid",
        )
        paths.add(relative)
        total += record["size"]
        _require(total <= MAX_BACKUP_BYTES, "Backup exceeds total size bound")
        _require(
            _record(root / relative, relative) == record,
            "Backup file checksum or size does not match",
        )
    _require(
        _DB_NAME in paths and _scan(root) == paths | {_MANIFEST},
        "Backup contains missing or unlisted files",
    )
    return manifest


def _apply_journal(
    connection: sqlite3.Connection,
    journal: dict[str, Any],
    snapshot_at: float,
    restored_at: float,
) -> dict[str, Any]:
    tombstones = journal["job_tombstones"]
    journal_deleted = {row["job_id"]: row for row in tombstones}
    for known in connection.execute(
        "SELECT job_id,user_id,deleted_at FROM job_tombstones"
    ):
        current = journal_deleted.get(known["job_id"])
        _require(
            current is not None
            and current["user_id"] == known["user_id"]
            and current["deleted_at"] >= known["deleted_at"],
            "Recovery journal omits or rolls back a known tombstone",
        )
    journal_revoked = {row["user_id"]: row for row in journal["account_revocations"]}
    for known in connection.execute(
        "SELECT user_id,updated_at FROM account_revocations"
    ):
        current = journal_revoked.get(known["user_id"])
        _require(
            current is not None and current["updated_at"] >= known["updated_at"],
            "Recovery journal omits or rolls back a known account revocation",
        )
    for row in tombstones:
        existing = connection.execute(
            "SELECT user_id FROM jobs WHERE id=?", (row["job_id"],)
        ).fetchone()
        _require(
            existing is None or existing[0] == row["user_id"],
            "Tombstone ownership conflicts with snapshot",
        )
        connection.execute(
            "INSERT INTO job_tombstones(job_id,user_id,deleted_at,reason) VALUES (?,?,?,?) "
            "ON CONFLICT(job_id) DO UPDATE SET deleted_at=MAX(deleted_at,excluded.deleted_at)",
            (row["job_id"], row["user_id"], row["deleted_at"], row["reason"]),
        )
    deleted = connection.execute(
        "DELETE FROM jobs WHERE id IN (SELECT job_id FROM job_tombstones)"
    ).rowcount
    for row in journal["account_revocations"]:
        connection.execute(
            "INSERT INTO account_revocations(user_id,disabled_at,is_active,updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET disabled_at=excluded.disabled_at,"
            "is_active=excluded.is_active,updated_at=excluded.updated_at",
            (row["user_id"], row["disabled_at"], row["is_active"], row["updated_at"]),
        )
    accounts = {row["user_id"]: row for row in journal["accounts"]}
    for user in connection.execute("SELECT id FROM users").fetchall():
        account = accounts.get(user[0])
        if account is None:
            connection.execute("UPDATE users SET is_active=0 WHERE id=?", (user[0],))
            connection.execute(
                "INSERT OR REPLACE INTO account_revocations VALUES (?,?,0,?)",
                (user[0], restored_at, restored_at),
            )
        else:
            connection.execute(
                "UPDATE users SET is_active=?,is_admin=?,daily_quota=?,max_file_bytes=? WHERE id=?",
                (
                    account["is_active"],
                    account["is_admin"],
                    account["daily_quota"],
                    account["max_file_bytes"],
                    user[0],
                ),
            )
    journal_resets = {
        row["user_id"]: row["reset_at"] for row in journal["password_resets"]
    }
    for known in connection.execute(
        "SELECT user_id,reset_at FROM recovery_password_resets"
    ):
        _require(
            journal_resets.get(known["user_id"], -1) >= known["reset_at"],
            "Recovery journal omits or rolls back a known password reset",
        )
    invalidated = 0
    for row in journal["password_resets"]:
        connection.execute(
            "INSERT INTO recovery_password_resets(user_id,reset_at) VALUES (?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET reset_at=MAX(reset_at,excluded.reset_at)",
            (row["user_id"], row["reset_at"]),
        )
        if row["reset_at"] >= snapshot_at:
            invalidated += connection.execute(
                "UPDATE users SET password_hash='!recovery-reset-required' WHERE id=?",
                (row["user_id"],),
            ).rowcount
    for row in journal["daily_usage"]:
        # Accounts created after T0 are absent; never recreate identities from a journal.
        connection.execute(
            "INSERT INTO daily_usage(user_id,day,used) SELECT ?,?,? "
            "WHERE EXISTS (SELECT 1 FROM users WHERE id=?) ON CONFLICT(user_id,day) "
            "DO UPDATE SET used=MAX(used,excluded.used)",
            (row["user_id"], row["day"], row["used"], row["user_id"]),
        )
    sessions = connection.execute("DELETE FROM sessions").rowcount
    invites = connection.execute(
        "UPDATE invites SET revoked_at=? WHERE used_at IS NULL AND revoked_at IS NULL",
        (restored_at,),
    ).rowcount
    interrupted = connection.execute(
        "UPDATE jobs SET status='failed',finished_at=?,error='恢复后请手动重试',metadata='{}' "
        "WHERE status IN ('queued','running')",
        (restored_at,),
    ).rowcount
    connection.execute(
        "UPDATE job_attempts SET state=CASE WHEN state='running' THEN 'interrupted' ELSE 'failed' END,"
        "finished_at=?,physical_released_at=?,error_code='backup_restored',error='恢复后请手动重试' "
        "WHERE state IN ('queued','running')",
        (restored_at, restored_at),
    )
    connection.execute(
        "UPDATE jobs SET status='expired',reserved_bytes=0,error=NULL,metadata='{}' WHERE expires_at<=?",
        (restored_at,),
    )
    return {
        "jobs_filtered": deleted,
        "sessions_invalidated": sessions,
        "invites_invalidated": invites,
        "passwords_invalidated": invalidated,
        "jobs_interrupted": interrupted,
    }


def restore_backup(
    backup_dir: Path,
    target_dir: Path,
    recovery_journal: Path,
    *,
    confirm_latest_journal: bool = False,
) -> dict[str, Any]:
    """Restore into a NEW/empty private directory, with mandatory current journal.

    Confirmation means the operator verified this journal captures every mutation
    through the last source shutdown. A newer timestamp alone cannot prove that.
    Loss of the current source plus an unproven journal is a hard recovery gap.
    """
    _require_private_permissions()
    _require(
        confirm_latest_journal is True,
        "Confirm the independent journal is latest through the final source shutdown; source-loss gaps cannot be bypassed",
    )
    root, destination, journal_path = (
        _path(backup_dir),
        _path(target_dir),
        _path(recovery_journal),
    )
    _separate(root, destination)
    _separate(root, journal_path)
    _separate(destination, journal_path)
    created = False
    complete = False
    try:
        manifest = _validate_snapshot(root)
        envelope = _read_json(journal_path)
        _fields(envelope, {"journal", "sha256"})
        journal = envelope["journal"]
        _validate_journal(journal)
        _require(
            envelope["sha256"] == hashlib.sha256(_json(journal)).hexdigest(),
            "Recovery journal checksum does not match",
        )
        _require(
            journal["source_id"] == manifest["source_id"],
            "Recovery journal belongs to another source",
        )
        _require(
            journal["exported_at"] >= manifest["created_at"],
            "Recovery journal predates the snapshot",
        )
        _new_directory(destination, allow_empty=True)
        created = True
        database_bytes = _read(root / _DB_NAME, MAX_DATABASE_BYTES)
        database_record = next(
            item for item in manifest["files"] if item["path"] == _DB_NAME
        )
        _require(
            hashlib.sha256(database_bytes).hexdigest() == database_record["sha256"],
            "Backup changed during restore",
        )
        _write(destination / _DB_NAME, database_bytes)
        connection = _connect(destination / _DB_NAME)
        try:
            _database_check(connection)
            _require(
                _identity(connection) == manifest["source_id"],
                "Backup database identity differs from manifest",
            )
            manifest_paths = {item["path"] for item in manifest["files"]} - {_DB_NAME}
            # Cross-check DB/file generation before filtering: a rewritten manifest
            # must not silently turn missing required source/output into a valid restore.
            expected_paths = (
                set(_artifacts(connection, root, manifest["created_at"]))
                if (root / "jobs").exists()
                else set()
            )
            if not (root / "jobs").exists():
                _require(
                    connection.execute(
                        "SELECT 1 FROM jobs WHERE status != 'expired' AND expires_at > ? LIMIT 1",
                        (manifest["created_at"],),
                    ).fetchone()
                    is None,
                    "Backup is missing required job files",
                )
            _require(
                manifest_paths == expected_paths,
                "Backup payloads do not match database generation",
            )
            _check_sources(connection, manifest["files"])
            restored_at = time.time()
            result = _apply_journal(
                connection, journal, manifest["created_at"], restored_at
            )
            keep = {
                row["id"]: row
                for row in _rows(
                    connection, "SELECT id,user_id,suffix,status,expires_at FROM jobs"
                )
            }
            accounts = {
                row["id"]: row["is_active"]
                for row in connection.execute("SELECT id,is_active FROM users")
            }
            (destination / "jobs").mkdir(mode=0o700)
            for record in manifest["files"]:
                relative = record["path"]
                if relative == _DB_NAME:
                    continue
                _, job_id, name = relative.split("/")
                row = keep.get(job_id)
                if (
                    row is None
                    or row["status"] == "expired"
                    or not accounts.get(row["user_id"])
                ):
                    continue
                _require(
                    name in {"source" + row["suffix"], "markdown.md", "preview.html"},
                    "Backup payload does not match its job",
                )
                if name != "source" + row["suffix"] and row["status"] != "succeeded":
                    continue
                content = _read(root / relative, _file_bound(relative))
                _require(
                    hashlib.sha256(content).hexdigest() == record["sha256"],
                    "Backup changed during restore",
                )
                path = destination / relative
                path.parent.mkdir(mode=0o700, exist_ok=True)
                _write(path, content)
            # Keep filtering durable even after an account is later reactivated
            # and an operator restores another older snapshot.
            connection.execute(
                "INSERT OR IGNORE INTO job_tombstones(job_id,user_id,deleted_at,reason) "
                "SELECT id,user_id,?,'restore_filtered' FROM jobs "
                "WHERE user_id IN (SELECT id FROM users WHERE is_active=0)",
                (restored_at,),
            )
            # Disabled accounts retain metadata only; no old document bytes reappear.
            connection.execute(
                "UPDATE jobs SET status='expired',reserved_bytes=0,metadata='{}',error=NULL "
                "WHERE user_id IN (SELECT id FROM users WHERE is_active=0)"
            )
            _database_check(connection)
            connection.commit()
        finally:
            connection.close()
        _sync_tree(destination)
        complete = True
        return {
            "snapshot_id": manifest["snapshot_id"],
            "restored_at": restored_at,
            **result,
        }
    except (OSError, sqlite3.Error, TypeError, KeyError) as error:
        raise RecoveryError(
            "Restore failed; existing data was not replaced; check backup and independent journal"
        ) from error
    finally:
        if created and not complete:
            shutil.rmtree(destination)
