"""Private, persistent conversion jobs with a bounded single-process scheduler.

SQLite transactions are the authority for ownership, quotas, capacity and state.
A process-wide file lock deliberately disallows multiple worker schedulers sharing
one data directory. Conversion subprocesses retain their existing resource limits.
"""

from __future__ import annotations

import io
import logging
import os
import re
import shutil
import stat
import threading
import time
import unicodedata
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from .conversion import (
    CONVERSION_TIMEOUT,
    MAX_HTML_BYTES,
    MAX_MARKDOWN_BYTES,
    ConversionError,
    run_conversion,
)

EXTENSIONS = frozenset({".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv", ".json"})
OUTPUT_RESERVATION = MAX_MARKDOWN_BYTES + MAX_HTML_BYTES
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
_LOG = logging.getLogger(__name__)


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


def _day(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).date().isoformat()


class JobService:
    def __init__(self, db: Any, settings: Any):
        self.db = db
        self.settings = settings
        self.root = Path(settings.data_dir).absolute()
        self._private_directory(self.root)
        self.jobs_dir = self.root / "jobs"
        self._private_directory(self.jobs_dir)
        self._lock = FileLock(str(self.root / "worker.lock"), thread_local=False)
        self._lifecycle = threading.RLock()
        self._active_lock = threading.Lock()
        self._inflight: dict[str, str] = {}
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
                    error TEXT
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
                """
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
            return (
                not path.is_symlink()
                and path.is_file()
                and path.stat().st_size == row["size"]
            )
        except (JobError, OSError):
            return False

    @staticmethod
    def _snapshot(row: Any) -> dict[str, Any]:
        return {
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
        }

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

    def _expire_due(self, connection: Any, now: float) -> None:
        expired = connection.execute(
            "SELECT id FROM jobs WHERE status != 'expired' AND expires_at <= ?", (now,)
        ).fetchall()
        for row in expired:
            self._remove_files(row["id"])
        connection.execute(
            "UPDATE jobs SET status = 'expired', reserved_bytes = 0, error = NULL, "
            "finished_at = COALESCE(finished_at, ?) WHERE status != 'expired' AND expires_at <= ?",
            (now, now),
        )

    def enqueue(
        self, user_id: str, files: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if (
            not isinstance(files, list)
            or not 1 <= len(files) <= self.settings.max_files
        ):
            raise JobError(400, "请选择允许数量的文件")
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
            if not data or len(data) > self.settings.max_file_bytes:
                raise JobError(413, "文件为空或超过单个文件大小限制")
            total += len(data)
            prepared.append(
                {
                    "id": uuid.uuid4().hex,
                    "filename": filename,
                    "suffix": suffix,
                    "data": data,
                }
            )
        if total > self.settings.max_total_bytes:
            raise JobError(413, "文件总大小超过限制")
        now = time.time()
        staged: list[str] = []
        try:
            # Admission and staging share a write transaction: rejected requests
            # never write source bytes and concurrent requests cannot over-reserve.
            with self.db.transaction() as connection:
                user = self._user(connection, user_id)
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
                for upload in prepared:
                    directory = self._job_dir(upload["id"])
                    directory.mkdir(mode=0o700)
                    staged.append(upload["id"])
                    self._write(
                        directory / ("source" + upload["suffix"]), upload["data"]
                    )
                    size = len(upload["data"])
                    connection.execute(
                        "INSERT INTO jobs(id,user_id,filename,suffix,size,reserved_bytes,status,created_at,expires_at) "
                        "VALUES (?,?,?,?,?,?,'queued',?,?)",
                        (
                            upload["id"],
                            user_id,
                            upload["filename"],
                            upload["suffix"],
                            size,
                            size + OUTPUT_RESERVATION,
                            now,
                            now + self.settings.retention_seconds,
                        ),
                    )
                results = [
                    self._snapshot(self._owned(connection, user_id, upload["id"]))
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
            return [self._snapshot(row) for row in rows]

    def get_job(
        self, user_id: str, job_id: str, include_content: bool = False
    ) -> dict[str, Any]:
        with self.db.transaction() as connection:
            self._expire_due(connection, time.time())
            row = self._owned(connection, user_id, job_id)
            result = self._snapshot(row)
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

    def retry(self, user_id: str, job_id: str) -> dict[str, Any]:
        now = time.time()
        with self.db.transaction() as connection:
            self._expire_due(connection, now)
            row = self._owned(connection, user_id, job_id)
            if row["status"] == "expired":
                raise JobError(410, "任务已过期")
            if (
                row["status"] != "failed"
                or row["attempts"] >= self.settings.max_attempts
            ):
                raise JobError(409, "此任务不能重试")
            user = self._user(connection, user_id)
            if not self._source_exists(row):
                raise JobError(410, "源文件不可用")
            self._capacity(connection, 1)
            self._charge(connection, user, 1, now)
            self._discard_outputs(row)
            connection.execute(
                "UPDATE jobs SET status='queued', error=NULL, started_at=NULL, finished_at=NULL WHERE id=?",
                (job_id,),
            )
            result = self._snapshot(self._owned(connection, user_id, job_id))
        self._wake.set()
        return result

    def delete_job(self, user_id: str, job_id: str) -> dict[str, Any]:
        with self.db.transaction() as connection:
            self._owned(connection, user_id, job_id)
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
        return _filename(Path(row["filename"]).stem + ".md"), data

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
        now = time.time()
        with self.db.transaction() as connection:
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
            return {
                "used": used,
                "daily_quota": user["daily_quota"],
                "remaining": max(0, user["daily_quota"] - used),
                "resets_at": tomorrow.timestamp(),
                "max_file_bytes": min(
                    user["max_file_bytes"], self.settings.max_file_bytes
                ),
                "active_jobs": active,
            }

    def cleanup(self, now: float | datetime | None = None) -> None:
        timestamp = (
            time.time()
            if now is None
            else (now.timestamp() if isinstance(now, datetime) else float(now))
        )
        with self.db.transaction() as connection:
            self._expire_due(connection, timestamp)
            for row in connection.execute(
                "SELECT id FROM jobs WHERE status='expired'"
            ).fetchall():
                self._remove_files(row["id"])
            connection.execute(
                "DELETE FROM jobs WHERE status='expired' AND expires_at < ?",
                (timestamp - self.settings.history_seconds,),
            )
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
                    except FileNotFoundError:
                        pass

    def _recover(self) -> None:
        now = time.time()
        with self.db.transaction() as connection:
            self._expire_due(connection, now)
            for row in connection.execute(
                "SELECT * FROM jobs WHERE status='running'"
            ).fetchall():
                error = "任务被中断，请重试"
                if row["attempts"] < self.settings.max_attempts and self._source_exists(
                    row
                ):
                    try:
                        user = self._user(connection, row["user_id"])
                        self._charge(connection, user, 1, now)
                        self._discard_outputs(row)
                    except JobError as exc:
                        error = exc.detail
                    else:
                        connection.execute(
                            "UPDATE jobs SET status='queued', started_at=NULL, finished_at=NULL, error=NULL WHERE id=?",
                            (row["id"],),
                        )
                        continue
                connection.execute(
                    "UPDATE jobs SET status='failed', finished_at=?, error=? WHERE id=?",
                    (now, error, row["id"]),
                )

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
            deadline = time.monotonic() + CONVERSION_TIMEOUT + 5
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
        now = time.time()
        with self.db.transaction() as connection:
            self._expire_due(connection, now)
            # A transient database/output failure after conversion can leave a
            # running row behind even though _execute has released its physical
            # slot. Recover that row without requiring an application restart.
            for running_row in connection.execute(
                "SELECT id FROM jobs WHERE status='running'"
            ).fetchall():
                if running_row["id"] not in self._inflight:
                    connection.execute(
                        "UPDATE jobs SET status='failed', finished_at=?, "
                        "error='任务执行被中断，请重试' WHERE id=? AND status='running'",
                        (now, running_row["id"]),
                    )
            connection.execute(
                "UPDATE jobs SET status='failed', finished_at=?, error='账号不可用' WHERE status='queued' "
                "AND NOT EXISTS (SELECT 1 FROM users WHERE users.id=jobs.user_id AND users.is_active=1)",
                (now,),
            )
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
                + "ORDER BY j.created_at, j.id LIMIT 1",
                (now, self.settings.per_user_concurrency, *blocked_users),
            ).fetchone()
            if row is None:
                return None
            if row["attempts"] >= self.settings.max_attempts or not self._source_exists(
                row
            ):
                connection.execute(
                    "UPDATE jobs SET status='failed', finished_at=?, error='源文件不可用或已达到重试上限' WHERE id=?",
                    (now, row["id"]),
                )
                return None
            connection.execute(
                "UPDATE jobs SET status='running', attempts=attempts+1, started_at=?, finished_at=NULL, error=NULL WHERE id=?",
                (now, row["id"]),
            )
            claimed = connection.execute(
                "SELECT * FROM jobs WHERE id=?", (row["id"],)
            ).fetchone()
        self._inflight[row["id"]] = row["user_id"]
        return claimed

    def _execute(self, row: Any) -> None:
        try:
            self._execute_claimed(row)
        finally:
            with self._active_lock:
                self._inflight.pop(row["id"], None)
            self._wake.set()

    def _execute_claimed(self, row: Any) -> None:
        markdown: bytes | None = None
        html: bytes | None = None
        error: str | None = None
        try:
            directory = self._job_dir(row["id"])
            # The existing subprocess writes this fixed result path. Creating it
            # privately first preserves mode 0600 when it truncates the file.
            self._write(directory / "result.json", b"")
            converted, preview = run_conversion(
                directory / ("source" + row["suffix"]), row["suffix"]
            )
            if not isinstance(converted, str) or not isinstance(preview, str):
                raise ConversionError("转换结果无效")
            markdown, html = converted.encode("utf-8"), preview.encode("utf-8")
            if len(markdown) > MAX_MARKDOWN_BYTES or len(html) > MAX_HTML_BYTES:
                raise ConversionError("转换结果过大，请拆分文档后重试")
        except ConversionError as exc:
            error = str(exc)[:300]
        except Exception:
            # Never expose parser exception details, paths, stack traces or input text.
            error = "无法转换此文件，可能已损坏、加密或超出资源限制"
        now = time.time()
        with self.db.transaction() as connection:
            self._expire_due(connection, now)
            current = connection.execute(
                "SELECT * FROM jobs WHERE id=?", (row["id"],)
            ).fetchone()
            if current is None or current["status"] != "running":
                if current is None or current["status"] == "expired":
                    self._remove_files(row["id"])
                return
            user = connection.execute(
                "SELECT is_active FROM users WHERE id=?", (row["user_id"],)
            ).fetchone()
            if user is None or not user["is_active"]:
                error = "账号不可用"
            try:
                self._discard_outputs(current)
                if error is None:
                    assert markdown is not None and html is not None
                    directory = self._job_dir(row["id"])
                    self._write(directory / "markdown.md", markdown)
                    self._write(directory / "preview.html", html)
            except (OSError, JobError):
                error = "无法保存转换结果，请稍后重试"
            connection.execute(
                "UPDATE jobs SET status=?, finished_at=?, error=? WHERE id=? AND status='running'",
                ("failed" if error else "succeeded", now, error, row["id"]),
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
                _LOG.exception("Job worker iteration failed")
            self._wake.wait(0.5)
            self._wake.clear()

    def _maintenance(self) -> None:
        interval = max(0.1, min(float(self.settings.cleanup_interval), 60.0))
        while not self._stop.wait(interval):
            try:
                self.cleanup()
            except Exception:
                _LOG.exception("Job cleanup failed")
