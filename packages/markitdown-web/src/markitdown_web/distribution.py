"""Public metadata verified against locally staged, real unsigned artifacts."""

import hashlib
import json
import os
import re
import stat
import threading
from functools import lru_cache
from pathlib import Path

MAX_MANIFEST_BYTES = 64 * 1024
MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
_DIGEST_LOCK = threading.Lock()


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_digest(path: str, identity: tuple[int, ...]) -> str:
    """Cache only stable file identities; replacements require a new digest."""
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or _identity(before) != identity:
            raise OSError("Artifact changed before verification")
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if _identity(os.fstat(stream.fileno())) != identity:
            raise OSError("Artifact changed during verification")
    return digest


_digest = lru_cache(maxsize=16)(_read_digest)


def downloads(static: Path) -> dict:
    manifest = static / "downloads.local.json"
    directory = static / "downloads"
    try:
        if manifest.is_symlink() or directory.is_symlink():
            return {"downloads": []}
        metadata = manifest.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_MANIFEST_BYTES:
            return {"downloads": []}
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        with os.fdopen(os.open(manifest, flags), "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return {"downloads": []}
            payload = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(payload) > MAX_MANIFEST_BYTES:
            return {"downloads": []}
        entries = json.loads(payload)
    except (OSError, ValueError):
        entries = []
    result = []
    if isinstance(entries, list):
        for entry in entries[:4]:
            if not isinstance(entry, dict):
                continue
            name, version, platform = (
                entry.get("filename"),
                entry.get("version"),
                entry.get("platform"),
            )
            if (
                not isinstance(name, str)
                or not isinstance(version, str)
                or len(version) > 40
                or not re.fullmatch(
                    r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?", version
                )
                or not isinstance(platform, str)
                or platform not in {"macos-arm64", "windows-x64"}
                or type(entry.get("bytes")) is not int
                or not 0 < entry["bytes"] <= MAX_ARTIFACT_BYTES
                or not isinstance(entry.get("sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])
            ):
                continue
            commit = entry.get("source_commit")
            if commit is not None and (
                not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40}", commit)
            ):
                continue
            target = "mac-arm64" if platform == "macos-arm64" else "win-x64"
            suffixes = (".dmg", ".zip") if platform == "macos-arm64" else (".exe",)
            if name not in {
                f"MarkItDown-{version}-{target}-unsigned{suffix}" for suffix in suffixes
            }:
                continue
            file = directory / name
            try:
                metadata = file.lstat()
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_size != entry["bytes"]
                ):
                    continue
                identity = _identity(metadata)
                # Concurrent requests share one bounded hash computation, rather
                # than repeatedly reading large installers in the public API.
                with _DIGEST_LOCK:
                    # Windows ctime is creation time, not a reliable change clock.
                    reader = _read_digest if os.name == "nt" else _digest
                    digest = reader(str(file), identity)
                if _identity(file.lstat()) != identity or digest != entry["sha256"]:
                    continue
            except OSError:
                continue
            result.append(
                {
                    "platform": platform,
                    "version": version,
                    "bytes": metadata.st_size,
                    "sha256": digest,
                    "url": f"/static/downloads/{name}",
                    "filename": name,
                    "signed": False,
                    **({"source_commit": commit} if commit is not None else {}),
                }
            )
    return {"downloads": result}
