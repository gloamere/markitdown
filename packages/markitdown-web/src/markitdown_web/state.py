"""Small, private single-instance persistence shared by auth and job services."""

import os
import sqlite3
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("MARKITDOWN_DATA_DIR", str(Path.cwd() / ".markitdown-data"))
        )
    )
    session_seconds: int = 12 * 60 * 60
    invite_seconds: int = 24 * 60 * 60
    default_daily_quota: int = 50
    default_max_file_bytes: int = 20 * 1024 * 1024
    cookie_secure: bool = field(
        default_factory=lambda: os.environ.get("MARKITDOWN_COOKIE_SECURE") == "1"
    )
    global_concurrency: int = 2
    per_user_concurrency: int = 1
    retention_seconds: int = 24 * 60 * 60
    history_seconds: int = 30 * 24 * 60 * 60
    cleanup_interval: float = 60
    max_storage_bytes: int = 1024 * 1024 * 1024
    max_queue_jobs: int = 100
    max_files: int = 10
    max_total_bytes: int = 50 * 1024 * 1024
    max_file_bytes: int = 20 * 1024 * 1024
    max_attempts: int = 3
    start_workers: bool = True
    docling_enabled: bool = field(
        default_factory=lambda: os.environ.get("MARKITDOWN_DOCLING_ENABLED") == "1"
    )
    docling_python: Path | None = field(
        default_factory=lambda: (
            Path(value)
            if (value := os.environ.get("MARKITDOWN_DOCLING_PYTHON"))
            else None
        )
    )
    docling_models: Path | None = field(
        default_factory=lambda: (
            Path(value)
            if (value := os.environ.get("MARKITDOWN_DOCLING_MODELS"))
            else None
        )
    )

    def __post_init__(self) -> None:
        root = Path(self.data_dir).expanduser().absolute()
        if root.is_symlink():
            raise ValueError("The private data directory cannot be a symlink")
        # Canonicalize trusted ancestors (e.g. macOS /var -> /private/var),
        # while services still reject symlinks within the private data tree.
        object.__setattr__(self, "data_dir", root.resolve())
        if not 1 <= self.global_concurrency <= 4:
            raise ValueError("global_concurrency must be between 1 and 4")
        if not 1 <= self.per_user_concurrency <= self.global_concurrency:
            raise ValueError("Invalid per-user concurrency")
        if self.retention_seconds <= 0 or self.cleanup_interval <= 0:
            raise ValueError("Retention and cleanup interval must be positive")
        if (
            self.max_file_bytes > 20 * 1024 * 1024
            or self.max_total_bytes > 50 * 1024 * 1024
        ):
            raise ValueError("Upload limits cannot exceed this release's hard limits")


class Database:
    def __init__(self, settings: Settings):
        root = settings.data_dir
        if root.is_symlink():
            raise ValueError("The private data directory cannot be a symlink")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        self.path = root / "workspace.sqlite3"
        if self.path.is_symlink():
            raise ValueError("The database cannot be a symlink")
        self.path.touch(mode=0o600, exist_ok=True)
        self.path.chmod(0o600)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            yield connection


def safe_filename(raw: str | None) -> str:
    name = (raw or "document").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(
        char
        for char in name
        if not unicodedata.category(char).startswith("C") and char not in '<>:"|?*'
    ).strip(" .")
    suffix = Path(name).suffix.lower()
    stem = name[: -len(suffix)] if suffix else name
    return (stem[:160].strip(" .") or "document") + suffix[:16]
