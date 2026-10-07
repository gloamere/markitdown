"""Public installer metadata from locally staged, real build artifacts only."""

import json
import re
from pathlib import Path


def downloads(static: Path) -> dict:
    manifest = static / "downloads.local.json"
    try:
        entries = json.loads(manifest.read_text())
    except (OSError, ValueError):
        entries = []
    result = []
    if isinstance(entries, list):
        for entry in entries[:4]:
            if not isinstance(entry, dict):
                continue
            name = entry.get("filename")
            if not isinstance(name, str) or not re.fullmatch(
                r"MarkItDown-[A-Za-z0-9_.-]+-unsigned\.(dmg|zip|exe)", name
            ):
                continue
            file = static / "downloads" / name
            try:
                valid_file = not file.is_symlink() and file.is_file()
                size = file.stat().st_size
            except OSError:
                continue
            if (
                not valid_file
                or size != entry.get("bytes")
                or entry.get("platform") not in {"macos-arm64", "windows-x64"}
                or not isinstance(entry.get("sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])
                or not isinstance(entry.get("version"), str)
                or len(entry["version"]) > 40
            ):
                continue
            result.append(
                {
                    "platform": entry["platform"],
                    "version": entry["version"],
                    "bytes": size,
                    "sha256": entry["sha256"],
                    "url": f"/static/downloads/{name}",
                    "filename": name,
                    "signed": False,
                }
            )
    return {"downloads": result}
