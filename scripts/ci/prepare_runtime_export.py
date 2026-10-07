"""Prepare only a fresh CI export of our just-built, trusted runtime image.

This is build-time packaging, never a runtime repair or a user-upload handler.
Docker creates /dev mount scaffolding in its container init layer, even when the
container is never started. The runtime still requires all seven targets empty.
See https://github.com/moby/moby/blob/v28.5.1/daemon/initlayer/setup_unix.go and
https://docs.docker.com/reference/cli/docker/container/export/.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

VERSION = "trusted-docker-export-v1"
APPLICATION_TARGETS = ("input", "output", "code", "models")
EPHEMERAL_TARGETS = ("tmp", "proc", "dev")
TARGETS = APPLICATION_TARGETS + EPHEMERAL_TARGETS
MARKER = ".runtime-export-pending.json"
MANIFEST = "markitdown-runtime.json"
REPORT = "runtime-preparation.json"
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class PreparationError(RuntimeError):
    """A content-free refusal; do not include paths or directory entry names."""


@contextmanager
def _directory(name: str, *, parent: int | None = None) -> Iterator[int]:
    fd = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _workspace(path: Path) -> Iterator[int]:
    runner_temp = Path(os.environ.get("RUNNER_TEMP", ""))
    if (
        not path.is_absolute()
        or ".." in path.parts
        or not runner_temp.is_absolute()
        or ".." in runner_temp.parts
        or path.parent != runner_temp
        or not path.name.startswith("markitdown-production.")
    ):
        raise PreparationError("not-a-dedicated-ci-workspace")
    # Open each component independently. Path.resolve() would hide symlinks.
    fd = os.open("/", DIRECTORY_FLAGS)
    try:
        for component in path.parts[1:]:
            child = os.open(component, DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        metadata = os.fstat(fd)
        if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise PreparationError("ci-workspace-must-be-private-and-owned")
        yield fd
    finally:
        os.close(fd)


def _exclusive_file(parent: int, name: str) -> int:
    return os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=parent,
    )


def _write_json(fd: int, value: dict) -> None:
    data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    with os.fdopen(os.dup(fd), "wb") as handle:
        handle.write(data)


def _read_marker(parent: int) -> dict:
    fd = os.open(MARKER, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    with os.fdopen(fd, "rb") as handle:
        metadata = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or metadata.st_size > 4096
        ):
            raise PreparationError("invalid-fresh-export-marker")
        marker = json.load(handle)
    if not isinstance(marker, dict):
        raise PreparationError("invalid-fresh-export-marker")
    return marker


def _identity(fd: int, image_id: str) -> dict:
    metadata = os.fstat(fd)
    return {
        "preparation_version": VERSION,
        "source_image_id": image_id,
        "root_device": metadata.st_dev,
        "root_inode": metadata.st_ino,
    }


def _image_id(value: str) -> None:
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
        raise PreparationError("invalid-source-image-id")


def initialize(workspace: Path, image_id: str) -> None:
    """Reserve an absent export root before extraction, with one-shot identity."""
    _image_id(image_id)
    with _workspace(workspace) as parent:
        # Never adopt an existing image, application directory, or prior run.
        os.mkdir("runtime-root", 0o755, dir_fd=parent)
        with _directory("runtime-root", parent=parent) as root:
            fd = _exclusive_file(parent, MARKER)
            try:
                _write_json(fd, _identity(root, image_id))
            finally:
                os.close(fd)


def _snapshot(root: int) -> dict:
    states = {}
    for target in TARGETS:
        try:
            metadata = os.stat(target, dir_fd=root, follow_symlinks=False)
        except FileNotFoundError:
            states[target] = {"type": "missing", "entries": 0}
            continue
        if stat.S_ISDIR(metadata.st_mode):
            with _directory(target, parent=root) as child:
                current = os.fstat(child)
                if (metadata.st_dev, metadata.st_ino) != (
                    current.st_dev,
                    current.st_ino,
                ) or current.st_dev != os.fstat(root).st_dev:
                    raise PreparationError("target-identity-or-device-changed")
                states[target] = {
                    "type": "directory",
                    "entries": len(os.listdir(child)),
                }
        else:
            states[target] = {
                "type": "symlink" if stat.S_ISLNK(metadata.st_mode) else "other",
                "entries": None,
            }
    return states


def _empty_directory(fd: int, device: int) -> None:
    # All traversal is relative to open, no-follow directory descriptors. A link
    # *inside* an ephemeral directory is unlinked, never opened or dereferenced;
    # hard-linked files are unlinked, never truncated/chmodded. No mounted tree.
    for name in os.listdir(fd):
        metadata = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            with _directory(name, parent=fd) as child:
                current = os.fstat(child)
                if current.st_dev != device or (current.st_dev, current.st_ino) != (
                    metadata.st_dev,
                    metadata.st_ino,
                ):
                    raise PreparationError("ephemeral-identity-or-device-changed")
                _empty_directory(child, device)
            os.rmdir(name, dir_fd=fd)
        else:
            os.unlink(name, dir_fd=fd)


def prepare(workspace: Path, image_id: str) -> dict:
    """Canonicalize fixed empty scaffolds, then attest packaging provenance."""
    _image_id(image_id)
    with _workspace(workspace) as parent, _directory(
        "runtime-root", parent=parent
    ) as root:
        identity = _identity(root, image_id)
        if _read_marker(parent) != identity:
            raise PreparationError("fresh-export-identity-mismatch")
        if os.fstat(root).st_dev != os.fstat(parent).st_dev:
            raise PreparationError("export-root-is-a-different-device")
        report = {
            "preparation_version": VERSION,
            "source_image_id": image_id,
            "status": "rejected",
        }
        report_fd = _exclusive_file(parent, REPORT)
        try:
            before = _snapshot(root)
            report["before"] = before
            try:
                os.stat(MANIFEST, dir_fd=root, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise PreparationError("export-already-has-a-runtime-manifest")
            # Validate ALL targets before deleting anything. Application state
            # is never repaired away. Only this explicitly reserved fresh image
            # export may have build/cache/Docker artifacts removed from tmp/proc/dev.
            for target, state in before.items():
                if state["type"] not in {"directory", "missing"}:
                    raise PreparationError("target-is-not-a-real-directory")
                if target in APPLICATION_TARGETS and state["entries"]:
                    raise PreparationError("unexpected-application-target-state")
            for target in TARGETS:
                if before[target]["type"] == "missing":
                    os.mkdir(target, 0o755, dir_fd=root)
                with _directory(target, parent=root) as child:
                    if target in EPHEMERAL_TARGETS:
                        _empty_directory(child, os.fstat(root).st_dev)
                    os.fchmod(child, 0o1777 if target == "tmp" else 0o755)
            report["after"] = _snapshot(root)
            if any(
                state != {"type": "directory", "entries": 0}
                for state in report["after"].values()
            ):
                raise PreparationError("prepared-target-validation-failed")
            manifest_fd = _exclusive_file(root, MANIFEST)
            try:
                _write_json(
                    manifest_fd,
                    {
                        "profile": "linux-bwrap-v1",
                        "image_id": image_id,
                        "preparation_version": VERSION,
                    },
                )
            finally:
                os.close(manifest_fd)
            os.unlink(MARKER, dir_fd=parent)
            report["status"] = "prepared"
            return report
        except PreparationError as exc:
            report["reason"] = str(exc)
            raise
        except (OSError, ValueError):
            report["reason"] = "filesystem-or-metadata-validation-failed"
            raise
        finally:
            _write_json(report_fd, report)
            os.close(report_fd)
            # Fixed target labels, types and counts only: no names or contents.
            print(json.dumps(report, sort_keys=True), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("initialize", "prepare"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--image-id", required=True)
    args = parser.parse_args()
    try:
        operation = initialize if args.action == "initialize" else prepare
        operation(args.workspace, args.image_id)
    except (PreparationError, OSError, ValueError):
        # Raw exceptions can contain filenames from the exported tree.
        parser.exit(
            1,
            "Trusted runtime export preparation refused; see content-free diagnostics.\n",
        )


if __name__ == "__main__":
    main()
