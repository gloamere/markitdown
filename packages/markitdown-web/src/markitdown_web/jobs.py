"""Private, persistent conversion jobs with a bounded single-process scheduler.

SQLite transactions are the authority for ownership, quotas, capacity and state.
A process-wide file lock deliberately disallows multiple worker schedulers sharing
one data directory. Conversion subprocesses retain their existing resource limits.
"""

from __future__ import annotations

import hashlib
import inspect
import io
import json
import logging
import math
import os
import re
import shutil
import stat
import sys
import threading
import time
import unicodedata
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable

from filelock import FileLock, Timeout

from .conversion import (
    CONVERSION_TIMEOUT,
    MAX_HTML_BYTES,
    MAX_MARKDOWN_BYTES,
    ConversionError,
    run_conversion,
)
from .engines import (
    ensure_engine_available,
    run_docling_conversion,
    validate_engine_uploads,
)

EXTENSIONS = frozenset({".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv", ".json"})
OUTPUT_RESERVATION = MAX_MARKDOWN_BYTES + MAX_HTML_BYTES
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
_LOG = logging.getLogger(__name__)
_SUBMISSION_LOCK = threading.Lock()
_SUBMISSIONS_IN_PROGRESS: dict[tuple[str, str, str], tuple[threading.Lock, int]] = {}


class JobError(Exception):
    """A safe error suitable for returning from the HTTP API."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _filename(raw: str) -> str:
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(
        char
        for char in name
        if not unicodedata.category(char).startswith("C") and char not in '<>:"|?*'
    ).strip(" .")
    suffix = Path(name).suffix.lower()
    stem = name[: -len(suffix)] if suffix else name
    return (stem[:160].strip(" .") or "document") + suffix[:16]


def _export_filename(raw: str) -> str:
    """Keep suggested download components portable without renaming the source."""
    name = _filename(raw)
    suffix = Path(name).suffix
    stem = name[: -len(suffix)] if suffix else name
    # Leave room for the ZIP task-ID/index suffix as well as .md. Count UTF-8
    # bytes, not Unicode code points; never split a multi-byte character.
    budget = 180 - len(suffix.encode("utf-8"))
    stem = stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore").rstrip(" .")
    # Windows reserves these basenames even when followed by an extension.
    # Check after shortening so trimming cannot create a reserved alias.
    if re.fullmatch(
        r"(?:CON|CONIN\$|CONOUT\$|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])",
        stem.split(".", 1)[0].rstrip(" "),
        re.I,
    ):
        stem = (
            ("document-" + stem)
            .encode("utf-8")[:budget]
            .decode("utf-8", errors="ignore")
            .rstrip(" .")
        )
    return (stem or "document") + suffix


def _day(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).date().isoformat()


class JobService:
    def __init__(
        self, db: Any, settings: Any, config_provider: Callable[..., dict] | None = None
    ):
        self.db = db
        self.settings = settings
        self.config_provider = config_provider
        self.root = Path(settings.data_dir).absolute()
        self._private_directory(self.root)
        self.jobs_dir = self.root / "jobs"
        self._private_directory(self.jobs_dir)
        self._lock = FileLock(str(self.root / "worker.lock"), thread_local=False)
        self._lifecycle = threading.RLock()
        self._active_lock = threading.Lock()
        self._inflight: dict[str, str] = {}
        self._inflight_engines: dict[str, str] = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []
        self._started = False
        with self.db.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id),
                    filename TEXT NOT NULL,
                    suffix TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    reserved_bytes INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('queued','running','succeeded','failed','expired')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    error TEXT,
                    engine TEXT NOT NULL DEFAULT 'markitdown',
                    metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS jobs_user_created ON jobs(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status, created_at);
                CREATE INDEX IF NOT EXISTS jobs_expiration ON jobs(expires_at);
                CREATE TABLE IF NOT EXISTS daily_usage (
                    user_id TEXT NOT NULL REFERENCES users(id),
                    day TEXT NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(user_id, day)
                );
                CREATE TABLE IF NOT EXISTS job_submissions (
                    user_id TEXT NOT NULL REFERENCES users(id),
                    key_hash TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    job_ids TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(user_id, key_hash)
                );
                CREATE TABLE IF NOT EXISTS job_attempts (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    state TEXT NOT NULL,
                    quota_charged INTEGER,
                    quota_day TEXT,
                    accepted_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    physical_released_at REAL,
                    error_code TEXT,
                    error TEXT,
                    snapshot TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    markdown_sha256 TEXT,
                    html_sha256 TEXT,
                    UNIQUE(job_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS job_retry_requests (
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    key_hash TEXT NOT NULL,
                    attempt_id TEXT NOT NULL REFERENCES job_attempts(id) ON DELETE CASCADE,
                    PRIMARY KEY(job_id, key_hash)
                );
                CREATE TABLE IF NOT EXISTS job_tombstones (
                    job_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    deleted_at REAL NOT NULL,
                    reason TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS job_maintenance (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    last_started_at REAL,
                    last_succeeded_at REAL,
                    last_failed_at REAL,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    last_error_code TEXT,
                    last_counts TEXT NOT NULL DEFAULT '{}'
                );
                INSERT OR IGNORE INTO job_maintenance(singleton) VALUES (1);
                """
            )

        # Additive migration preserves job IDs, ownership, status constraints and
        # all existing auth/quota data. Serialize concurrent service initialization.
        with self.db.transaction() as connection:
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(jobs)")
            }
            if "engine" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN engine TEXT NOT NULL DEFAULT 'markitdown'"
                )
            if "metadata" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'"
                )
            for name, declaration in {
                "source_sha256": "TEXT",
                "submission_snapshot": "TEXT",
                "current_attempt_id": "TEXT",
            }.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE jobs ADD COLUMN {name} {declaration}"
                    )
            maintenance_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(job_maintenance)")
            }
            for name, declaration in {
                "worker_failures": "INTEGER NOT NULL DEFAULT 0",
                "last_worker_failed_at": "REAL",
                "last_worker_error_code": "TEXT",
            }.items():
                if name not in maintenance_columns:
                    connection.execute(
                        f"ALTER TABLE job_maintenance ADD COLUMN {name} {declaration}"
                    )

    @staticmethod
    def _private_directory(path: Path) -> None:
        # Reject existing symlink components, including the configured data root.
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise RuntimeError("Job storage must not contain symbolic links")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not path.is_dir():
            raise RuntimeError("Job storage is not a directory")
        path.chmod(0o700)

    def _job_dir(self, job_id: str) -> Path:
        if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
            raise JobError(404, "任务不存在")
        directory = self.jobs_dir / job_id
        if (
            any(parent.is_symlink() for parent in (self.root, self.jobs_dir, directory))
            or directory.resolve() != directory
        ):
            raise JobError(410, "任务文件不可用")
        return directory

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        temporary = path.with_name(".write-" + uuid.uuid4().hex)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _read(path: Path, maximum: int) -> bytes:
        flags = (
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
                    raise JobError(410, "任务文件不可用")
                data = source.read(maximum + 1)
                if len(data) > maximum:
                    raise JobError(410, "任务文件不可用")
                return data
        except OSError as exc:
            raise JobError(410, "任务文件不可用") from exc

    def _remove_files(self, job_id: str) -> None:
        # rmtree does not traverse a symlink at the root. Unlink that case explicitly.
        if not _JOB_ID.fullmatch(job_id):
            return
        if self.root.is_symlink() or self.jobs_dir.is_symlink():
            raise RuntimeError("Job storage changed unexpectedly")
        path = self.jobs_dir / job_id
        if path.is_symlink():
            path.unlink(missing_ok=True)
        elif path.exists():
            shutil.rmtree(path)

    def _discard_outputs(self, row: Any) -> None:
        directory = self._job_dir(row["id"])
        if not directory.exists():
            return
        for path in directory.iterdir():
            if path.name == "source" + row["suffix"]:
                continue
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)

    def _source_exists(self, row: Any) -> bool:
        try:
            path = self._job_dir(row["id"]) / ("source" + row["suffix"])
            exists = (
                not path.is_symlink()
                and path.is_file()
                and path.stat().st_size == row["size"]
            )
            if exists and row["source_sha256"]:
                data = self._read(path, self.settings.max_file_bytes)
                return hashlib.sha256(data).hexdigest() == row["source_sha256"]
            return exists
        except (JobError, OSError):
            return False

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    @staticmethod
    def _recorded_json(raw: Any) -> dict[str, Any] | None:
        # Snapshots are application-authored, bounded records, never worker JSON.
        if not isinstance(raw, str) or len(raw) > 16384:
            return None
        try:
            value = json.loads(raw)
        except (ValueError, RecursionError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _package_version(package: str) -> str | None:
        try:
            return version(package)
        except PackageNotFoundError:
            return None

    def _execution_snapshot(
        self, engine: str, connection: Any = None
    ) -> dict[str, Any]:
        configuration = (
            self.config_provider(connection=connection)
            if self.config_provider is not None
            else {}
        )
        retention = configuration.get(
            "retention_seconds", self.settings.retention_seconds
        )
        if (
            not isinstance(retention, (int, float))
            or isinstance(retention, bool)
            or retention <= 0
            or not math.isfinite(retention)
        ):
            raise JobError(503, "服务配置不可用")
        snapshot: dict[str, Any] = {
            "schema_version": 1,
            "config_version": configuration.get("version"),
            "engine": engine,
            "profile": "pdf-layout-local-v3"
            if engine == "docling"
            else "markitdown-local-v1",
            "ocr": False,
            "engine_version": (
                "2.133.0"
                if engine == "docling"
                else self._package_version("markitdown")
            ),
            "version_source": "approved_runtime"
            if engine == "docling"
            else "installed_package",
            "supervisor_versions": {
                "python": ".".join(str(part) for part in sys.version_info[:3]),
                "markitdown_web": self._package_version("markitdown-web"),
                "markitdown": self._package_version("markitdown"),
            },
            "limits": {
                "retention_seconds": retention,
                "max_file_bytes": min(self.settings.max_file_bytes, 10 * 1024**2)
                if engine == "docling"
                else self.settings.max_file_bytes,
                "max_markdown_bytes": MAX_MARKDOWN_BYTES,
                "max_html_bytes": MAX_HTML_BYTES,
                "max_pages": 2 if engine == "docling" else None,
                "wall_seconds": 60 if engine == "docling" else CONVERSION_TIMEOUT,
                "max_attempts": self.settings.max_attempts,
            },
            "deployment": {
                "global_concurrency": self.settings.global_concurrency,
                "per_user_concurrency": self.settings.per_user_concurrency,
                "max_queue_jobs": self.settings.max_queue_jobs,
                "max_storage_bytes": self.settings.max_storage_bytes,
            },
        }
        try:
            from .sandbox import execution_snapshot
        except ImportError:
            snapshot["deployment"]["sandbox_profile"] = "unrecorded"
        else:
            boundary = execution_snapshot(self.settings, engine)
            for key in ("sandbox_profile", "isolation", "production_boundary"):
                if isinstance(boundary.get(key), (str, bool)):
                    snapshot["deployment"][key] = boundary[key]
            image_id = boundary.get("runtime_image_id")
            if isinstance(image_id, str) and re.fullmatch(
                r"[A-Za-z0-9._:+-]{1,200}", image_id
            ):
                snapshot["deployment"]["runtime_image_id"] = image_id
            if engine == "markitdown" and boundary.get("production_boundary") is True:
                snapshot["engine_version"] = None
                snapshot["version_source"] = "unrecorded_until_execution"
        snapshot["snapshot_sha256"] = hashlib.sha256(
            self._json(snapshot).encode()
        ).hexdigest()
        return snapshot

    def _new_attempt(
        self,
        connection: Any,
        job_id: str,
        now: float,
        reason: str,
        snapshot: dict[str, Any] | None,
        *,
        charged: bool | None = True,
    ) -> str:
        attempt_id = uuid.uuid4().hex
        sequence = connection.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 FROM job_attempts WHERE job_id=?",
            (job_id,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO job_attempts(id,job_id,sequence,reason,state,quota_charged,quota_day,accepted_at,snapshot) "
            "VALUES (?,?,?,?,'queued',?,?,?,?)",
            (
                attempt_id,
                job_id,
                sequence,
                reason,
                int(charged) if charged is not None else None,
                _day(now) if charged else None,
                now,
                self._json(snapshot) if snapshot is not None else None,
            ),
        )
        connection.execute(
            "UPDATE jobs SET current_attempt_id=? WHERE id=?", (attempt_id, job_id)
        )
        return attempt_id

    def _finish_attempt(
        self,
        connection: Any,
        row: Any,
        state: str,
        now: float,
        error_code: str | None = None,
        error: str | None = None,
    ) -> None:
        if row["current_attempt_id"]:
            connection.execute(
                "UPDATE job_attempts SET state=?,finished_at=COALESCE(finished_at,?),"
                "error_code=?,error=? WHERE id=? AND state IN ('queued','running','cancel_requested')",
                (state, now, error_code, error, row["current_attempt_id"]),
            )

    @staticmethod
    def _error_code(error: str | None) -> str | None:
        if error is None:
            return None
        for needle, code in (
            ("取消", "cancelled"),
            ("账号", "account_disabled"),
            ("超时", "timeout"),
            ("未就绪", "engine_unavailable"),
            ("环境", "engine_unavailable"),
            ("过大", "output_limit"),
            ("没有提取", "no_text"),
            ("保存", "storage_error"),
            ("中断", "interrupted"),
        ):
            if needle in error:
                return code
        return "conversion_failed"

    @staticmethod
    def _safe_metadata(value: Any) -> dict[str, Any]:
        # Never persist/return arbitrary worker fields (paths, source content, or
        # environment details). The small allowlist is also applied on DB reads.
        if isinstance(value, str):
            if len(value) > 4096:
                return {}
            try:
                value = json.loads(value)
            except (TypeError, ValueError, RecursionError):
                return {}
        if not isinstance(value, dict):
            return {}
        safe: dict[str, Any] = {}
        engine = value.get("engine")
        if isinstance(engine, str) and engine in {"markitdown", "docling"}:
            safe["engine"] = engine
        version = value.get("version")
        if isinstance(version, str) and re.fullmatch(r"[0-9A-Za-z.+_-]{1,64}", version):
            safe["version"] = version
        python_version = value.get("python_version")
        if isinstance(python_version, str) and re.fullmatch(
            r"[0-9A-Za-z.+_-]{1,64}", python_version
        ):
            safe["python_version"] = python_version
        if value.get("version_source") == "worker_reported":
            safe["version_source"] = "worker_reported"
        pages = value.get("page_count")
        if type(pages) is int and 0 <= pages <= 100000:
            safe["page_count"] = pages
        duration = value.get("duration_seconds")
        if (
            isinstance(duration, (int, float))
            and not isinstance(duration, bool)
            and 0 <= duration <= 86400
            and math.isfinite(duration)
        ):
            safe["duration_seconds"] = round(duration, 3)
        if isinstance(value.get("profile"), str) and value["profile"] in {
            "pdf-layout-local-v3",
            "markitdown-local-v1",
        }:
            safe["profile"] = value["profile"]
        if value.get("ocr") is False:
            safe["ocr"] = False
        if value.get("warnings") == []:
            safe["warnings"] = []
        if value.get("quality_assessment") == "not_evaluated":
            safe["quality_assessment"] = "not_evaluated"
        for key in (
            "source_sha256",
            "markdown_sha256",
            "html_sha256",
            "snapshot_sha256",
        ):
            if isinstance(value.get(key), str) and re.fullmatch(
                r"[0-9a-f]{64}", value[key]
            ):
                safe[key] = value[key]
        for key in ("markdown_bytes", "html_bytes"):
            if type(value.get(key)) is int and 0 <= value[key] <= MAX_HTML_BYTES:
                safe[key] = value[key]
        return safe

    @staticmethod
    def _engine(engine: Any) -> str:
        if not isinstance(engine, str) or engine not in {"markitdown", "docling"}:
            raise JobError(400, "不支持此转换引擎")
        return engine

    def _available(self, engine: str) -> None:
        self._engine(engine)
        try:
            ensure_engine_available(engine, self.settings)
        except ConversionError as exc:
            raise JobError(400, str(exc)[:300]) from exc

    def _snapshot(self, row: Any, connection: Any = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": row["id"],
            "filename": row["filename"],
            "suffix": row["suffix"],
            "size": row["size"],
            "size_bytes": row["size"],
            "status": row["status"],
            "attempts": row["attempts"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "error": row["error"],
            "engine": row["engine"],
            "metadata": JobService._safe_metadata(row["metadata"]),
            "source_sha256": row["source_sha256"],
            "submission_snapshot": self._recorded_json(row["submission_snapshot"]),
            "current_attempt_id": row["current_attempt_id"],
            "provenance_status": "recorded"
            if row["submission_snapshot"]
            else "unrecorded",
        }
        if connection is None:
            with self.db.connect() as reader:
                history = self._attempt_history(reader, row["id"])
        else:
            history = self._attempt_history(connection, row["id"])
        result["attempt_history"] = history
        result["attempt_history_complete"] = bool(row["submission_snapshot"])
        result["accepted_attempts"] = len(history)
        current = next(
            (item for item in history if item["id"] == row["current_attempt_id"]), None
        )
        result["quota_charged"] = current["quota_charged"] if current else None
        result["error_code"] = current["error_code"] if current else None
        result["lifecycle_status"] = (
            "stopping"
            if row["status"] in {"failed", "expired"} and row["id"] in self._inflight
            else "cancelled"
            if current and current["state"] == "cancelled"
            else row["status"]
        )
        return result

    def _attempt_history(self, connection: Any, job_id: str) -> list[dict[str, Any]]:
        records = []
        for row in connection.execute(
            "SELECT * FROM job_attempts WHERE job_id=? ORDER BY sequence", (job_id,)
        ):
            record = dict(row)
            record.pop("job_id")
            record["quota_charged"] = (
                bool(record["quota_charged"])
                if record["quota_charged"] is not None
                else None
            )
            record["snapshot"] = self._recorded_json(record["snapshot"])
            record["metadata"] = self._safe_metadata(record["metadata"])
            record["provenance_status"] = (
                "recorded" if record["snapshot"] else "unrecorded"
            )
            records.append(record)
        return records

    def list_attempts(self, user_id: str, job_id: str) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            self._owned(connection, user_id, job_id)
            return self._attempt_history(connection, job_id)

    @staticmethod
    def _user(connection: Any, user_id: str) -> Any:
        row = connection.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None or not row["is_active"]:
            raise JobError(403, "账号不可用")
        return row

    @staticmethod
    def _owned(connection: Any, user_id: str, job_id: str) -> Any:
        JobService._user(connection, user_id)
        if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
            raise JobError(404, "任务不存在")
        row = connection.execute(
            "SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id)
        ).fetchone()
        if row is None:
            raise JobError(404, "任务不存在")
        return row

    @staticmethod
    def _charge(connection: Any, user: Any, count: int, now: float) -> None:
        day = _day(now)
        used = connection.execute(
            "SELECT used FROM daily_usage WHERE user_id = ? AND day = ?",
            (user["id"], day),
        ).fetchone()
        if (used["used"] if used else 0) + count > user["daily_quota"]:
            raise JobError(429, "今日转换配额已用完，请明天再试")
        connection.execute(
            "INSERT INTO daily_usage(user_id, day, used) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, day) DO UPDATE SET used = used + excluded.used",
            (user["id"], day, count),
        )

    def _capacity(self, connection: Any, count: int, reservation: int = 0) -> None:
        active = connection.execute(
            "SELECT COUNT(*) AS count FROM jobs WHERE status IN ('queued','running')"
        ).fetchone()["count"]
        if active + count > self.settings.max_queue_jobs:
            raise JobError(429, "任务队列已满，请稍后重试")
        used = connection.execute(
            "SELECT COALESCE(SUM(reserved_bytes), 0) AS used FROM jobs WHERE status != 'expired'"
        ).fetchone()["used"]
        if used + reservation > self.settings.max_storage_bytes:
            raise JobError(507, "存储空间不足，请删除旧任务后重试")

    def _expire_due(self, connection: Any, now: float) -> int:
        expired = connection.execute(
            "SELECT * FROM jobs WHERE status != 'expired' AND expires_at <= ?", (now,)
        ).fetchall()
        for row in expired:
            self._finish_attempt(connection, row, "expired", now, "expired", "任务已过期")
            self._remove_files(row["id"])
        connection.execute(
            "UPDATE jobs SET status = 'expired', reserved_bytes = 0, error = NULL, "
            "finished_at = COALESCE(finished_at, ?) WHERE status != 'expired' AND expires_at <= ?",
            (now, now),
        )
        return len(expired)

    def _replay_submission(
        self,
        connection: Any,
        user_id: str,
        key_hash: str | None,
        payload_hash: str,
    ) -> list[dict[str, Any]] | None:
        if key_hash is None:
            return None
        previous = connection.execute(
            "SELECT * FROM job_submissions WHERE user_id=? AND key_hash=?",
            (user_id, key_hash),
        ).fetchone()
        if previous is None:
            return None
        if previous["payload_hash"] != payload_hash:
            raise JobError(409, "提交标识已用于不同内容，请重新提交")
        results = []
        for job_id in json.loads(previous["job_ids"]):
            row = connection.execute(
                "SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)
            ).fetchone()
            if row is None:
                raise JobError(410, "此前提交的任务已删除或历史已清理，请使用新的提交标识")
            results.append(self._snapshot(row, connection))
        return results

    def enqueue(
        self,
        user_id: str,
        files: list[dict[str, Any]],
        engine: str = "markitdown",
        submission_key: str | None = None,
    ) -> list[dict[str, Any]]:
        # Serialize identical in-process preflights too: a network replay must
        # not spuriously fail the single Docling preflight slot while its first
        # request is still validating. SQLite remains the durable authority.
        if submission_key is None:
            return self._enqueue(user_id, files, engine, None)
        if not isinstance(submission_key, str) or not re.fullmatch(
            r"[A-Za-z0-9._:-]{1,128}", submission_key
        ):
            raise JobError(400, "提交标识无效")
        identity = (
            str(self.db.path),
            user_id,
            hashlib.sha256(submission_key.encode()).hexdigest(),
        )
        with _SUBMISSION_LOCK:
            lock, users = _SUBMISSIONS_IN_PROGRESS.get(identity, (threading.Lock(), 0))
            _SUBMISSIONS_IN_PROGRESS[identity] = (lock, users + 1)
        try:
            with lock:
                return self._enqueue(user_id, files, engine, submission_key)
        finally:
            with _SUBMISSION_LOCK:
                _, users = _SUBMISSIONS_IN_PROGRESS[identity]
                if users == 1:
                    del _SUBMISSIONS_IN_PROGRESS[identity]
                else:
                    _SUBMISSIONS_IN_PROGRESS[identity] = (lock, users - 1)

    def _enqueue(
        self,
        user_id: str,
        files: list[dict[str, Any]],
        engine: str = "markitdown",
        submission_key: str | None = None,
    ) -> list[dict[str, Any]]:
        if (
            not isinstance(files, list)
            or not 1 <= len(files) <= self.settings.max_files
        ):
            raise JobError(400, "请选择允许数量的文件")
        if submission_key is not None and (
            not isinstance(submission_key, str)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", submission_key)
        ):
            raise JobError(400, "提交标识无效")
        key_hash = (
            hashlib.sha256(submission_key.encode()).hexdigest()
            if submission_key
            else None
        )
        engine = self._engine(engine)
        prepared: list[dict[str, Any]] = []
        total = 0
        for upload in files:
            if not isinstance(upload, dict):
                raise JobError(400, "无效的文件")
            raw, suffix, data = (
                upload.get("filename"),
                upload.get("suffix"),
                upload.get("data"),
            )
            if (
                not isinstance(raw, str)
                or not isinstance(suffix, str)
                or not isinstance(data, bytes)
            ):
                raise JobError(400, "无效的文件")
            filename = _filename(raw)
            suffix = suffix.lower()
            if suffix not in EXTENSIONS or Path(filename).suffix.lower() != suffix:
                raise JobError(400, "暂不支持此文件格式")
            total += len(data)
            prepared.append(
                {
                    "id": uuid.uuid4().hex,
                    "filename": filename,
                    "suffix": suffix,
                    "data": data,
                    "source_sha256": hashlib.sha256(data).hexdigest(),
                    "request_filename": raw,
                }
            )
        payload_hash = hashlib.sha256(
            self._json(
                {
                    "engine": engine,
                    "files": [
                        {
                            key: upload[key]
                            for key in ("request_filename", "suffix", "source_sha256")
                        }
                        for upload in prepared
                    ],
                }
            ).encode()
        ).hexdigest()
        # Authenticate before external preflight, but do not hold SQLite's writer
        # lock while the bounded PDF/runtime validator runs. Admission rechecks
        # the account and all mutable limits afterward.
        with self.db.transaction() as connection:
            user = self._user(connection, user_id)
            self._expire_due(connection, time.time())
            replay = self._replay_submission(
                connection, user_id, key_hash, payload_hash
            )
            if replay is not None:
                return replay
            if self._stop.is_set():
                raise JobError(503, "服务正在停止，请稍后重试")
            if total > self.settings.max_total_bytes:
                raise JobError(413, "文件总大小超过限制")
            if any(
                not upload["data"] or len(upload["data"]) > self.settings.max_file_bytes
                for upload in prepared
            ):
                raise JobError(413, "文件为空或超过单个文件大小限制")
            if any(len(upload["data"]) > user["max_file_bytes"] for upload in prepared):
                raise JobError(413, "文件超过账号的单个文件大小限制")
        self._available(engine)
        try:
            validate_engine_uploads(engine, prepared, self.settings)
        except ConversionError as exc:
            raise JobError(400, str(exc)[:300]) from exc
        staged: list[str] = []
        try:
            # Admission and staging share a write transaction: rejected requests
            # never write source bytes and concurrent requests cannot over-reserve.
            with self.db.transaction() as connection:
                now = time.time()
                user = self._user(connection, user_id)
                replay = self._replay_submission(
                    connection, user_id, key_hash, payload_hash
                )
                if replay is not None:
                    return replay
                if self._stop.is_set():
                    raise JobError(503, "服务正在停止，请稍后重试")
                if any(
                    len(upload["data"]) > user["max_file_bytes"] for upload in prepared
                ):
                    raise JobError(413, "文件超过账号的单个文件大小限制")
                self._expire_due(connection, now)
                self._capacity(
                    connection,
                    len(prepared),
                    total + len(prepared) * OUTPUT_RESERVATION,
                )
                self._charge(connection, user, len(prepared), now)
                snapshot = self._execution_snapshot(engine, connection)
                for upload in prepared:
                    directory = self._job_dir(upload["id"])
                    directory.mkdir(mode=0o700)
                    staged.append(upload["id"])
                    self._write(
                        directory / ("source" + upload["suffix"]), upload["data"]
                    )
                    size = len(upload["data"])
                    connection.execute(
                        "INSERT INTO jobs(id,user_id,filename,suffix,size,reserved_bytes,status,created_at,expires_at,engine,source_sha256,submission_snapshot) "
                        "VALUES (?,?,?,?,?,?,'queued',?,?,?,?,?)",
                        (
                            upload["id"],
                            user_id,
                            upload["filename"],
                            upload["suffix"],
                            size,
                            size + OUTPUT_RESERVATION,
                            now,
                            now + snapshot["limits"]["retention_seconds"],
                            engine,
                            upload["source_sha256"],
                            self._json(snapshot),
                        ),
                    )
                    self._new_attempt(
                        connection, upload["id"], now, "submission", snapshot
                    )
                if key_hash is not None:
                    connection.execute(
                        "INSERT INTO job_submissions(user_id,key_hash,payload_hash,job_ids,created_at) VALUES (?,?,?,?,?)",
                        (
                            user_id,
                            key_hash,
                            payload_hash,
                            self._json([item["id"] for item in prepared]),
                            now,
                        ),
                    )
                results = [
                    self._snapshot(
                        self._owned(connection, user_id, upload["id"]), connection
                    )
                    for upload in prepared
                ]
        except BaseException:
            for job_id in staged:
                self._remove_files(job_id)
            raise
        self._wake.set()
        return results

    def list_jobs(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        with self.db.transaction() as connection:
            self._user(connection, user_id)
            self._expire_due(connection, time.time())
            rows = connection.execute(
                "SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [self._snapshot(row, connection) for row in rows]

    def get_job(
        self, user_id: str, job_id: str, include_content: bool = False
    ) -> dict[str, Any]:
        with self.db.transaction() as connection:
            self._expire_due(connection, time.time())
            row = self._owned(connection, user_id, job_id)
            result = self._snapshot(row, connection)
            if include_content:
                if row["status"] == "expired":
                    raise JobError(410, "任务已过期")
                if row["status"] == "succeeded":
                    directory = self._job_dir(job_id)
                    try:
                        result["markdown"] = self._read(
                            directory / "markdown.md", MAX_MARKDOWN_BYTES
                        ).decode("utf-8")
                        result["html"] = self._read(
                            directory / "preview.html", MAX_HTML_BYTES
                        ).decode("utf-8")
                    except UnicodeError as exc:
                        raise JobError(410, "任务文件不可用") from exc
            return result

    def _retryable(self, row: Any) -> None:
        if row["status"] == "expired" or row["expires_at"] <= time.time():
            raise JobError(410, "任务已过期")
        if row["status"] != "failed" or row["attempts"] >= self.settings.max_attempts:
            raise JobError(409, "此任务不能重试")
        if not self._source_exists(row):
            raise JobError(410, "源文件不可用")

    def retry(
        self, user_id: str, job_id: str, submission_key: str | None = None
    ) -> dict[str, Any]:
        if submission_key is not None and (
            not isinstance(submission_key, str)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", submission_key)
        ):
            raise JobError(400, "提交标识无效")
        key_hash = (
            hashlib.sha256(submission_key.encode()).hexdigest()
            if submission_key
            else None
        )

        def replay(connection: Any, row: Any) -> dict[str, Any] | None:
            if (
                key_hash is not None
                and connection.execute(
                    "SELECT 1 FROM job_retry_requests WHERE job_id=? AND key_hash=?",
                    (job_id, key_hash),
                ).fetchone()
            ):
                return self._snapshot(row, connection)
            return None

        with self.db.connect() as connection:
            row = self._owned(connection, user_id, job_id)
            previous = replay(connection, row)
            if previous is not None:
                return previous
            if self._stop.is_set():
                raise JobError(503, "服务正在停止，请稍后重试")
            self._retryable(row)
        self._available(row["engine"])
        # A canceled job can become publicly failed before its process exits.
        # Reusing its directory/job ID during that interval would let the old
        # converter race the retry and incorrectly release its physical slot.
        with self._active_lock, self.db.transaction() as connection:
            now = time.time()
            self._expire_due(connection, now)
            current = self._owned(connection, user_id, job_id)
            previous = replay(connection, current)
            if previous is not None:
                return previous
            if self._stop.is_set():
                raise JobError(503, "服务正在停止，请稍后重试")
            self._retryable(current)
            if job_id in self._inflight:
                raise JobError(409, "任务正在停止，请稍后重试")
            accepted = connection.execute(
                "SELECT COUNT(*) FROM job_attempts WHERE job_id=?", (job_id,)
            ).fetchone()[0]
            if accepted >= self.settings.max_attempts:
                raise JobError(409, "此任务已达到重试上限")
            user = self._user(connection, user_id)
            self._capacity(connection, 1)
            self._charge(connection, user, 1, now)
            self._discard_outputs(current)
            self._finish_attempt(
                connection,
                current,
                "failed",
                now,
                self._error_code(current["error"]),
                current["error"],
            )
            attempt_id = self._new_attempt(
                connection,
                job_id,
                now,
                "retry",
                self._execution_snapshot(current["engine"], connection),
            )
            if key_hash is not None:
                connection.execute(
                    "INSERT INTO job_retry_requests(job_id,key_hash,attempt_id) VALUES (?,?,?)",
                    (job_id, key_hash, attempt_id),
                )
            connection.execute(
                "UPDATE jobs SET status='queued', error=NULL, metadata='{}', started_at=NULL, finished_at=NULL WHERE id=?",
                (job_id,),
            )
            result = self._snapshot(
                self._owned(connection, user_id, job_id), connection
            )
        self._wake.set()
        return result

    def cancel(self, user_id: str, job_id: str) -> dict[str, Any]:
        with self.db.transaction() as connection:
            now = time.time()
            self._expire_due(connection, now)
            row = self._owned(connection, user_id, job_id)
            if row["status"] == "expired":
                raise JobError(410, "任务已过期")
            if row["status"] not in {"queued", "running"}:
                raise JobError(409, "此任务不能取消")
            self._finish_attempt(
                connection, row, "cancelled", now, "cancelled", "任务已取消"
            )
            connection.execute(
                "UPDATE jobs SET status='failed', finished_at=?, error='任务已取消', metadata='{}' WHERE id=?",
                (now, job_id),
            )
            result = self._snapshot(
                self._owned(connection, user_id, job_id), connection
            )
        # Cancellation does not refund quota or release a physical worker slot.
        self._wake.set()
        return result

    def delete_job(self, user_id: str, job_id: str) -> dict[str, Any]:
        with self.db.transaction() as connection:
            self._owned(connection, user_id, job_id)
            connection.execute(
                "INSERT OR IGNORE INTO job_tombstones(job_id,user_id,deleted_at,reason) VALUES (?,?,?,'user_deleted')",
                (job_id, user_id, time.time()),
            )
            self._remove_files(job_id)
            connection.execute(
                "DELETE FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)
            )
        # The physical in-flight slot is deliberately released only by _execute.
        # Daily accepted-job usage is also retained: deletion never refunds quota.
        return {"id": job_id, "deleted": True}

    def _download(
        self, connection: Any, user_id: str, job_id: str
    ) -> tuple[str, bytes]:
        row = self._owned(connection, user_id, job_id)
        if row["status"] == "expired" or row["expires_at"] <= time.time():
            raise JobError(410, "任务已过期")
        if row["status"] != "succeeded":
            raise JobError(409, "任务尚未完成")
        data = self._read(self._job_dir(job_id) / "markdown.md", MAX_MARKDOWN_BYTES)
        return _export_filename(Path(row["filename"]).stem + ".md"), data

    def download(self, user_id: str, job_id: str) -> tuple[str, bytes]:
        with self.db.transaction() as connection:
            self._expire_due(connection, time.time())
            return self._download(connection, user_id, job_id)

    def archive(self, user_id: str, ids: list[str]) -> bytes:
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 10
            or any(not isinstance(value, str) for value in ids)
        ):
            raise JobError(400, "请选择 1 至 10 个任务")
        if len(set(ids)) != len(ids):
            raise JobError(400, "不能重复选择任务")
        with self.db.transaction() as connection:
            self._expire_due(connection, time.time())
            # Resolve every owner first, so a mixed request never leaks another user's state.
            for job_id in ids:
                self._owned(connection, user_id, job_id)
            files = [
                (job_id, *self._download(connection, user_id, job_id)) for job_id in ids
            ]
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for index, (job_id, filename, data) in enumerate(files, 1):
                archive.writestr(
                    f"{Path(filename).stem}-{job_id[:12]}-{index}.md", data
                )
        return output.getvalue()

    def get_usage(self, user_id: str) -> dict[str, Any]:
        with self.db.transaction() as connection:
            now = time.time()
            self._expire_due(connection, now)
            user = self._user(connection, user_id)
            row = connection.execute(
                "SELECT used FROM daily_usage WHERE user_id=? AND day=?",
                (user_id, _day(now)),
            ).fetchone()
            used = row["used"] if row else 0
            active = connection.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE user_id=? AND status IN ('queued','running')",
                (user_id,),
            ).fetchone()["count"]
            tomorrow = datetime.fromtimestamp(now, timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) + timedelta(days=1)
            configuration = (
                self.config_provider(connection=connection)
                if self.config_provider is not None
                else {}
            )
            return {
                "used": used,
                "daily_quota": user["daily_quota"],
                "remaining": max(0, user["daily_quota"] - used),
                "resets_at": tomorrow.timestamp(),
                "max_file_bytes": min(
                    user["max_file_bytes"], self.settings.max_file_bytes
                ),
                "active_jobs": active,
                "retention_seconds": configuration.get(
                    "retention_seconds", self.settings.retention_seconds
                ),
                "config_version": configuration.get("version"),
                "max_attempts": self.settings.max_attempts,
            }

    def cleanup(self, now: float | datetime | None = None) -> None:
        timestamp = (
            time.time()
            if now is None
            else (now.timestamp() if isinstance(now, datetime) else float(now))
        )
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE job_maintenance SET last_started_at=? WHERE singleton=1",
                (timestamp,),
            )
        try:
            counts = self._cleanup(timestamp)
        except Exception:
            with self.db.transaction() as connection:
                connection.execute(
                    "UPDATE job_maintenance SET last_failed_at=?,consecutive_failures=consecutive_failures+1,"
                    "last_error_code='cleanup_failed' WHERE singleton=1",
                    (timestamp,),
                )
            raise
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE job_maintenance SET last_succeeded_at=?,consecutive_failures=0,"
                "last_error_code=NULL,last_counts=? WHERE singleton=1",
                (timestamp, self._json(counts)),
            )

    def cleanup_status(self) -> dict[str, Any]:
        """Safe operator diagnostics; contains no user filenames or content."""
        with self.db.connect() as connection:
            result = dict(
                connection.execute(
                    "SELECT * FROM job_maintenance WHERE singleton=1"
                ).fetchone()
            )
        result.pop("singleton")
        result["last_counts"] = self._recorded_json(result["last_counts"]) or {}
        return result

    def _cleanup(self, timestamp: float) -> dict[str, int]:
        counts = {
            "expired_jobs": 0,
            "history_rows_removed": 0,
            "orphan_job_dirs_removed": 0,
            "orphan_scratch_dirs_removed": 0,
        }
        with self.db.transaction() as connection:
            counts["expired_jobs"] = self._expire_due(connection, timestamp)
            for row in connection.execute(
                "SELECT id FROM jobs WHERE status='expired'"
            ).fetchall():
                self._remove_files(row["id"])
            connection.execute(
                "INSERT OR IGNORE INTO job_tombstones(job_id,user_id,deleted_at,reason) "
                "SELECT id,user_id,?,'history_expired' FROM jobs WHERE status='expired' AND expires_at < ?",
                (timestamp, timestamp - self.settings.history_seconds),
            )
            removed = connection.execute(
                "DELETE FROM jobs WHERE status='expired' AND expires_at < ?",
                (timestamp - self.settings.history_seconds,),
            )
            counts["history_rows_removed"] = removed.rowcount
            connection.execute(
                "DELETE FROM daily_usage WHERE day < ?",
                (_day(timestamp - self.settings.history_seconds),),
            )
            known = {row["id"] for row in connection.execute("SELECT id FROM jobs")}
            # Interrupted upload staging is private and never accessible. Allow an hour
            # for an in-flight request before removing directories with no database row.
            for path in self.jobs_dir.iterdir():
                if _JOB_ID.fullmatch(path.name) and path.name not in known:
                    try:
                        if path.lstat().st_mtime < timestamp - 3600:
                            self._remove_files(path.name)
                            counts["orphan_job_dirs_removed"] += 1
                    except FileNotFoundError:
                        pass
            # Only the supervisor's known prefix is eligible. A live parser
            # retains its registry entry through actual process reaping, even
            # if the OS cannot stop it within the advertised wall-clock bound.
            from .sandbox import active_workspaces

            active = active_workspaces()
            if self.root.is_symlink():
                raise RuntimeError("Job storage changed unexpectedly")
            for path in self.root.iterdir():
                if (
                    not re.fullmatch(
                        r"(?:engine|preview)-[A-Za-z0-9_-]{6,64}", path.name
                    )
                    or path in active
                ):
                    continue
                try:
                    if path.lstat().st_mtime >= timestamp - 3600:
                        continue
                    if path.is_symlink():
                        path.unlink()
                    elif path.is_dir():
                        shutil.rmtree(path)
                    else:
                        continue
                    counts["orphan_scratch_dirs_removed"] += 1
                except FileNotFoundError:
                    pass
        return counts

    def _fail_job(
        self, connection: Any, row: Any, now: float, error: str, code: str | None = None
    ) -> None:
        self._finish_attempt(
            connection, row, "failed", now, code or self._error_code(error), error
        )
        connection.execute(
            "UPDATE jobs SET status='failed', finished_at=?, error=?, metadata='{}' WHERE id=?",
            (now, error, row["id"]),
        )

    def _recover(self) -> None:
        # A queued admission was already charged; an interrupted execution needs
        # a separate counted attempt. All prior attempt rows remain immutable.
        with self.db.connect() as connection:
            pending = connection.execute(
                "SELECT * FROM jobs WHERE status IN ('queued','running')"
            ).fetchall()
        readiness: dict[str, str | None] = {}
        for candidate in pending:
            engine = candidate["engine"]
            if engine not in readiness:
                try:
                    self._available(engine)
                except JobError as exc:
                    readiness[engine] = exc.detail
                else:
                    readiness[engine] = None
        now = time.time()
        with self.db.transaction() as connection:
            self._expire_due(connection, now)
            for candidate in pending:
                row = connection.execute(
                    "SELECT * FROM jobs WHERE id=? AND status IN ('queued','running')",
                    (candidate["id"],),
                ).fetchone()
                if row is None:
                    continue
                unavailable = readiness[row["engine"]]
                try:
                    user = self._user(connection, row["user_id"])
                except JobError as exc:
                    self._fail_job(connection, row, now, exc.detail, "account_disabled")
                    continue
                if row["status"] == "queued":
                    if unavailable is not None:
                        self._fail_job(
                            connection, row, now, unavailable, "engine_unavailable"
                        )
                    continue
                self._finish_attempt(
                    connection, row, "interrupted", now, "service_restart", "任务被服务重启中断"
                )
                connection.execute(
                    "UPDATE job_attempts SET physical_released_at=COALESCE(physical_released_at,?) WHERE id=?",
                    (now, row["current_attempt_id"]),
                )
                accepted = connection.execute(
                    "SELECT COUNT(*) FROM job_attempts WHERE job_id=?", (row["id"],)
                ).fetchone()[0]
                error = unavailable or "任务被中断，请重试"
                if (
                    unavailable is None
                    and max(accepted, row["attempts"]) < self.settings.max_attempts
                    and self._source_exists(row)
                ):
                    try:
                        # Prepare all fallible work before quota mutation; an
                        # unavailable source/config must not consume a recovery.
                        snapshot = self._execution_snapshot(row["engine"], connection)
                        self._discard_outputs(row)
                        self._charge(connection, user, 1, now)
                    except JobError as exc:
                        error = exc.detail
                    else:
                        self._new_attempt(
                            connection, row["id"], now, "restart_recovery", snapshot
                        )
                        connection.execute(
                            "UPDATE jobs SET status='queued', started_at=NULL, finished_at=NULL, error=NULL, metadata='{}' WHERE id=?",
                            (row["id"],),
                        )
                        continue
                self._fail_job(connection, row, now, error)

    def start(self) -> None:
        with self._lifecycle:
            if self._started:
                return
            if (self.root / "worker.lock").is_symlink():
                raise RuntimeError("Worker lock must not be a symbolic link")
            try:
                self._lock.acquire(timeout=0)
            except Timeout as exc:
                raise RuntimeError(
                    "Another worker service already owns this data directory"
                ) from exc
            try:
                (self.root / "worker.lock").chmod(0o600)
                self.cleanup()
                self._recover()
                self._stop.clear()
                self._wake.clear()
                concurrency = max(1, int(self.settings.global_concurrency))
                self._threads = [
                    threading.Thread(
                        target=self._worker, name=f"conversion-{index}", daemon=True
                    )
                    for index in range(concurrency)
                ]
                self._threads.append(
                    threading.Thread(
                        target=self._maintenance, name="conversion-cleanup", daemon=True
                    )
                )
                self._started = True
                for thread in self._threads:
                    thread.start()
            except BaseException:
                self._stop.set()
                self._wake.set()
                self._lock.release()
                self._started = False
                raise

    def stop(self) -> None:
        with self._lifecycle:
            if not self._started:
                return
            self._stop.set()
            self._wake.set()
            # Docling has a longer wall-clock bound but cancellation normally
            # interrupts it immediately. Retain the lock if any worker survives.
            deadline = time.monotonic() + max(CONVERSION_TIMEOUT, 60) + 5
            for thread in self._threads:
                thread.join(max(0, deadline - time.monotonic()))
            if any(thread.is_alive() for thread in self._threads):
                # Never allow a new scheduler to overlap a still-running conversion.
                raise RuntimeError(
                    "Conversion workers have not stopped; worker lock retained"
                )
            self._lock.release()
            self._threads.clear()
            self._started = False

    def _claim(self) -> Any | None:
        # A deleted/expired running job remains physically in flight until the
        # bounded converter exits, even though its public state is already expired.
        with self._active_lock:
            return self._claim_locked()

    def _claim_locked(self) -> Any | None:
        if len(self._inflight) >= self.settings.global_concurrency:
            return None
        blocked_users = sorted(
            {
                user_id
                for user_id in self._inflight.values()
                if list(self._inflight.values()).count(user_id)
                >= self.settings.per_user_concurrency
            }
        )
        exclusion = ""
        if blocked_users:
            exclusion = (
                "AND j.user_id NOT IN (" + ",".join("?" for _ in blocked_users) + ") "
            )
        # Runtime checks are performed outside the write transaction. The
        # converter repeats readiness checks before process startup as well.
        with self.db.connect() as connection:
            engines = [
                row["engine"]
                for row in connection.execute(
                    "SELECT DISTINCT engine FROM jobs WHERE status='queued'"
                )
            ]
        unavailable: dict[str, str] = {}
        for engine in engines:
            try:
                self._available(engine)
            except JobError as exc:
                unavailable[engine] = exc.detail
        engine_exclusion = (
            "AND j.engine != 'docling' "
            if "docling" in self._inflight_engines.values()
            else ""
        )
        with self.db.transaction() as connection:
            # Another writer may submit a job while this worker waits for the
            # lock. Sample after acquisition so its start cannot predate creation.
            now = time.time()
            self._expire_due(connection, now)
            for engine, error in unavailable.items():
                for failed in connection.execute(
                    "SELECT * FROM jobs WHERE status='queued' AND engine=?", (engine,)
                ).fetchall():
                    self._fail_job(connection, failed, now, error, "engine_unavailable")
            # A transient database/output failure after conversion can leave a
            # running row behind even though _execute has released its physical
            # slot. Recover that row without requiring an application restart.
            for running_row in connection.execute(
                "SELECT * FROM jobs WHERE status='running'"
            ).fetchall():
                if running_row["id"] not in self._inflight:
                    self._fail_job(
                        connection, running_row, now, "任务执行被中断，请重试", "interrupted"
                    )
            for disabled in connection.execute(
                "SELECT * FROM jobs WHERE status='queued' "
                "AND NOT EXISTS (SELECT 1 FROM users WHERE users.id=jobs.user_id AND users.is_active=1)"
            ).fetchall():
                self._fail_job(connection, disabled, now, "账号不可用", "account_disabled")
            running = connection.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE status='running'"
            ).fetchone()["count"]
            if running >= self.settings.global_concurrency:
                return None
            row = connection.execute(
                "SELECT j.* FROM jobs j JOIN users u ON j.user_id=u.id "
                "WHERE j.status='queued' AND j.expires_at>? AND u.is_active=1 "
                "AND (SELECT COUNT(*) FROM jobs r WHERE r.user_id=j.user_id AND r.status='running') < ? "
                + exclusion
                + engine_exclusion
                + "ORDER BY j.created_at, j.id LIMIT 1",
                (now, self.settings.per_user_concurrency, *blocked_users),
            ).fetchone()
            if row is None:
                return None
            if row["attempts"] >= self.settings.max_attempts or not self._source_exists(
                row
            ):
                self._fail_job(
                    connection, row, now, "源文件不可用或已达到重试上限", "source_unavailable"
                )
                return None
            if row["current_attempt_id"] is None:
                self._new_attempt(
                    connection,
                    row["id"],
                    now,
                    "legacy",
                    self._execution_snapshot(row["engine"], connection),
                    charged=None,
                )
                row = connection.execute(
                    "SELECT * FROM jobs WHERE id=?", (row["id"],)
                ).fetchone()
            recorded = connection.execute(
                "SELECT snapshot FROM job_attempts WHERE id=?",
                (row["current_attempt_id"],),
            ).fetchone()
            snapshot = self._recorded_json(recorded["snapshot"]) if recorded else None
            current_snapshot = self._execution_snapshot(row["engine"], connection)
            if snapshot is not None and any(
                snapshot.get(key) != current_snapshot.get(key)
                for key in (
                    "engine_version",
                    "profile",
                    "supervisor_versions",
                    "deployment",
                )
            ):
                self._fail_job(
                    connection, row, now, "提交时的转换环境已变更，请重试以使用当前版本", "profile_changed"
                )
                return None
            connection.execute(
                "UPDATE jobs SET status='running', attempts=attempts+1, started_at=?, finished_at=NULL, error=NULL WHERE id=?",
                (now, row["id"]),
            )
            connection.execute(
                "UPDATE job_attempts SET state='running',started_at=? WHERE id=? AND state='queued'",
                (now, row["current_attempt_id"]),
            )
            claimed = connection.execute(
                "SELECT * FROM jobs WHERE id=?", (row["id"],)
            ).fetchone()
        self._inflight[row["id"]] = row["user_id"]
        self._inflight_engines[row["id"]] = row["engine"]
        return claimed

    def _execute(self, row: Any) -> None:
        try:
            self._execute_claimed(row)
        finally:
            with self._active_lock:
                try:
                    with self.db.transaction() as connection:
                        connection.execute(
                            "UPDATE job_attempts SET physical_released_at=COALESCE(physical_released_at,?) WHERE id=?",
                            (time.time(), row["current_attempt_id"]),
                        )
                finally:
                    self._inflight.pop(row["id"], None)
                    self._inflight_engines.pop(row["id"], None)
            self._wake.set()

    def _cancelled(self, row: Any) -> bool:
        if self._stop.is_set():
            return True
        try:
            with self.db.connect() as connection:
                # Keep cancellation polling short even during database pressure;
                # uncertainty fails closed instead of extending the child limit.
                connection.execute("PRAGMA busy_timeout=50")
                current = connection.execute(
                    "SELECT j.status,j.expires_at,j.current_attempt_id,u.is_active FROM jobs j "
                    "JOIN users u ON j.user_id=u.id WHERE j.id=? AND j.user_id=?",
                    (row["id"], row["user_id"]),
                ).fetchone()
            return (
                current is None
                or current["status"] != "running"
                or current["current_attempt_id"] != row["current_attempt_id"]
                or current["expires_at"] <= time.time()
                or not current["is_active"]
            )
        except Exception:
            # Losing authority to establish that the job is still runnable must
            # not allow an expensive child to continue indefinitely.
            return True

    def _execute_claimed(self, row: Any) -> None:
        metadata: dict[str, Any] = {}
        runtime_metadata: dict[str, Any] = {}
        markdown: bytes | None = None
        html: bytes | None = None
        error: str | None = None
        started = time.monotonic()
        try:
            if self._cancelled(row):
                raise ConversionError("任务已取消")
            directory = self._job_dir(row["id"])
            # The existing subprocess writes this fixed result path. Creating it
            # privately first preserves mode 0600 when it truncates the file.
            self._write(directory / "result.json", b"")
            source = directory / ("source" + row["suffix"])
            if row["engine"] == "docling":
                converted, preview, details = run_docling_conversion(
                    source, self.settings, cancelled=lambda: self._cancelled(row)
                )
                metadata = self._safe_metadata(details)
            elif row["engine"] == "markitdown":
                # The real supervisor always receives both controls. Keep small
                # legacy converter test doubles compatible without catching and
                # retrying TypeError (which might repeat an actual conversion).
                parameters = inspect.signature(run_conversion).parameters
                kwargs: dict[str, Any] = {}
                if "cancelled" in parameters or any(
                    p.kind == p.VAR_KEYWORD for p in parameters.values()
                ):
                    kwargs["cancelled"] = lambda: self._cancelled(row)
                if "settings" in parameters or any(
                    p.kind == p.VAR_KEYWORD for p in parameters.values()
                ):
                    kwargs["settings"] = self.settings
                if "runtime_metadata" in parameters or any(
                    p.kind == p.VAR_KEYWORD for p in parameters.values()
                ):
                    kwargs["runtime_metadata"] = runtime_metadata
                converted, preview = run_conversion(source, row["suffix"], **kwargs)
                metadata = {
                    "engine": "markitdown",
                    "profile": "markitdown-local-v1",
                    "ocr": False,
                }
                if runtime_metadata:
                    metadata.update(
                        {
                            "version": runtime_metadata.get("version"),
                            "python_version": runtime_metadata.get("python"),
                            "version_source": "worker_reported",
                        }
                    )
            else:
                raise ConversionError("不支持此转换引擎")
            if not isinstance(converted, str) or not isinstance(preview, str):
                raise ConversionError("转换结果无效")
            markdown, html = converted.encode("utf-8"), preview.encode("utf-8")
            if len(markdown) > MAX_MARKDOWN_BYTES or len(html) > MAX_HTML_BYTES:
                raise ConversionError("转换结果过大，请拆分文档后重试")
            if not converted.strip():
                raise ConversionError("没有提取到文字；本次未开启 OCR，请检查文件")
            metadata.setdefault(
                "duration_seconds", round(time.monotonic() - started, 3)
            )
            metadata.update(
                {
                    "quality_assessment": "not_evaluated",
                    "source_sha256": row["source_sha256"],
                    "markdown_sha256": hashlib.sha256(markdown).hexdigest(),
                    "html_sha256": hashlib.sha256(html).hexdigest(),
                    "markdown_bytes": len(markdown),
                    "html_bytes": len(html),
                }
            )
            metadata = self._safe_metadata(metadata)
        except ConversionError as exc:
            error = str(exc)[:300]
        except Exception:
            # Never expose parser exception details, paths, stack traces or input text.
            error = "无法转换此文件，可能已损坏、加密或超出资源限制"
        if runtime_metadata and error:
            metadata = self._safe_metadata(
                {
                    "engine": row["engine"],
                    "profile": "markitdown-local-v1",
                    "version": runtime_metadata.get("version"),
                    "python_version": runtime_metadata.get("python"),
                    "version_source": "worker_reported",
                    "ocr": False,
                    "duration_seconds": round(time.monotonic() - started, 3),
                }
            )
        execution_metadata = {
            key: value
            for key, value in metadata.items()
            if key
            in {
                "engine",
                "profile",
                "version",
                "python_version",
                "version_source",
                "ocr",
                "duration_seconds",
            }
        }
        now = time.time()
        with self.db.transaction() as connection:
            self._expire_due(connection, now)
            current = connection.execute(
                "SELECT * FROM jobs WHERE id=?", (row["id"],)
            ).fetchone()
            if (
                current is None
                or current["status"] != "running"
                or current["current_attempt_id"] != row["current_attempt_id"]
            ):
                connection.execute(
                    "UPDATE job_attempts SET metadata=? WHERE id=?",
                    (self._json(execution_metadata), row["current_attempt_id"]),
                )
                if current is None or current["status"] == "expired":
                    self._remove_files(row["id"])
                elif current["status"] == "failed":
                    self._discard_outputs(current)
                return
            user = connection.execute(
                "SELECT is_active FROM users WHERE id=?", (row["user_id"],)
            ).fetchone()
            if user is None or not user["is_active"]:
                error = "账号不可用"
            if self._stop.is_set() and error is None:
                error = "任务因服务停止而中断，请重试"
            try:
                self._discard_outputs(current)
                if error is None:
                    assert markdown is not None and html is not None
                    directory = self._job_dir(row["id"])
                    self._write(directory / "markdown.md", markdown)
                    self._write(directory / "preview.html", html)
            except (OSError, JobError):
                error = "无法保存转换结果，请稍后重试"
                self._discard_outputs(current)
            state = "failed" if error else "succeeded"
            self._finish_attempt(
                connection, current, state, now, self._error_code(error), error
            )
            connection.execute(
                "UPDATE job_attempts SET metadata=?,markdown_sha256=?,html_sha256=? WHERE id=?",
                (
                    self._json(execution_metadata if error else metadata),
                    None if error else metadata.get("markdown_sha256"),
                    None if error else metadata.get("html_sha256"),
                    row["current_attempt_id"],
                ),
            )
            connection.execute(
                "UPDATE jobs SET status=?, finished_at=?, error=?, metadata=? WHERE id=? AND status='running'",
                (
                    state,
                    now,
                    error,
                    json.dumps({} if error else metadata, ensure_ascii=False),
                    row["id"],
                ),
            )
        self._wake.set()

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                row = self._claim()
                if row is not None:
                    self._execute(row)
                    continue
            except Exception:
                # Logs must not contain parser content, source paths, or raw
                # exception text. Safe counters remain available to operators.
                _LOG.error("Job worker iteration failed [worker_iteration_failed]")
                try:
                    with self.db.transaction() as connection:
                        connection.execute(
                            "UPDATE job_maintenance SET worker_failures=worker_failures+1,"
                            "last_worker_failed_at=?,last_worker_error_code='worker_iteration_failed' WHERE singleton=1",
                            (time.time(),),
                        )
                except Exception:
                    _LOG.error(
                        "Worker diagnostics unavailable [diagnostics_write_failed]"
                    )
            self._wake.wait(0.5)
            self._wake.clear()

    def _maintenance(self) -> None:
        interval = max(0.1, min(float(self.settings.cleanup_interval), 60.0))
        while not self._stop.wait(interval):
            try:
                self.cleanup()
            except Exception:
                _LOG.error("Job cleanup failed [cleanup_failed]")
