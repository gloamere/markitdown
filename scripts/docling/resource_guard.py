#!/usr/bin/env python3
"""Bound runtime disk use and supervise an installer subprocess (standard library)."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

GIB = 1024**3
MAX_RUNTIME_BYTES = 8 * GIB
MIN_FREE_BYTES = 10 * GIB


def tree_bytes(root: Path) -> int:
    """Count hardlinked uv cache/venv files once and never follow symlinks."""
    seen = set()
    total = 0
    for directory, _, files in os.walk(root, followlinks=False):
        for name in files:
            path = Path(directory) / name
            try:
                stat = path.lstat()
            except FileNotFoundError:
                continue  # Installers atomically move temporary files.
            if path.is_symlink():
                continue
            key = (stat.st_dev, stat.st_ino)
            if key not in seen:
                seen.add(key)
                total += max(stat.st_size, stat.st_blocks * 512)
    return total


def check(root: Path, reserve: int = 0) -> dict:
    used = tree_bytes(root)
    free = shutil.disk_usage(root).free
    result = {"runtime_bytes": used, "free_bytes": free}
    if used + reserve > MAX_RUNTIME_BYTES:
        raise RuntimeError(
            f"Runtime disk budget exceeded: {used + reserve} > {MAX_RUNTIME_BYTES}"
        )
    if free - reserve < MIN_FREE_BYTES:
        raise RuntimeError(
            f"Less than 10 GiB free would remain: {free - reserve} bytes"
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    print(json.dumps(check(args.root)), flush=True)
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        return
    proc = subprocess.Popen(command, start_new_session=True)
    try:
        while proc.poll() is None:
            check(args.root)
            time.sleep(1)
        check(args.root)
    except BaseException:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        raise
    raise SystemExit(proc.returncode)


if __name__ == "__main__":
    main()
