"""Stage real unsigned builds and optional version-bound evidence; never publish."""

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "packages/markitdown-web/src/markitdown_web/static"


def source_commit(evidence: Path | None, entry: dict) -> str | None:
    if evidence is None:
        return None
    if evidence.is_symlink() or not evidence.is_file():
        raise ValueError("Build evidence must be a regular non-symlink file")
    with evidence.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Build evidence is too large")
    report = json.loads(raw)
    if (
        not isinstance(report, dict)
        or type(report.get("schema_version")) is not int
        or report.get("schema_version") != 1
        or report.get("kind") != "markitdown_desktop_build_evidence"
        or not isinstance(report.get("source"), dict)
        or report["source"].get("clean") is not True
        or not all(
            isinstance(report["source"].get(key), str)
            and re.fullmatch(r"[0-9a-f]{40}", report["source"][key])
            for key in ("commit", "tree")
        )
    ):
        raise ValueError("Build evidence has no valid clean-source identity")
    platform, arch = (
        ("macos", "arm64") if entry["platform"] == "macos-arm64" else ("windows", "x64")
    )
    expected = {key: entry[key] for key in ("filename", "version", "bytes", "sha256")}
    expected.update(platform=platform, arch=arch)
    artifact = report.get("artifact")
    if (
        not isinstance(artifact, dict)
        or type(artifact.get("bytes")) is not int
        or any(artifact.get(key) != value for key, value in expected.items())
    ):
        raise ValueError("Build evidence does not match this artifact")
    return report["source"]["commit"]


def stage_artifacts(
    inputs: list[tuple], version: str, static: Path = STATIC
) -> list[dict]:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?", version):
        raise ValueError("Invalid desktop version")
    destination = static / "downloads"
    if destination.is_symlink():
        raise ValueError("The artifact directory must not be a symlink")
    prepared = []
    # Validate every input before replacing a previous staging manifest or file.
    for file, platform, evidence in inputs:
        os_name, arch, suffix = (
            ("mac", "arm64", ".dmg")
            if platform == "macos-arm64"
            else ("win", "x64", ".exe")
        )
        if platform not in {"macos-arm64", "windows-x64"}:
            raise ValueError("Unsupported artifact platform")
        expected = f"MarkItDown-{version}-{os_name}-{arch}-unsigned{suffix}"
        if file.is_symlink() or not file.is_file() or file.name != expected:
            raise ValueError(f"Expected a real non-symlink artifact named {expected}")
        size = file.stat().st_size
        if not 0 < size <= 1024 * 1024 * 1024:
            raise ValueError("Expected a nonempty artifact of at most 1 GiB")
        with file.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        entry = {
            "platform": platform,
            "filename": expected,
            "version": version,
            "bytes": size,
            "sha256": digest,
        }
        commit = source_commit(evidence, entry)
        if commit is not None:
            entry["source_commit"] = commit
        prepared.append((file, entry))
    if not prepared:
        raise ValueError("Provide at least one actual build artifact")
    destination.mkdir(exist_ok=True)
    staged = []
    temporary_manifest = None
    try:
        for file, entry in prepared:
            with tempfile.NamedTemporaryFile(dir=destination, delete=False) as stream:
                temporary = Path(stream.name)
                staged.append((temporary, entry))
                with file.open("rb") as source:
                    shutil.copyfileobj(source, stream)
                stream.flush()
                os.fsync(stream.fileno())
            with temporary.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if temporary.stat().st_size != entry["bytes"] or digest != entry["sha256"]:
                raise ValueError("Artifact changed while staging")
        # Each installer is replaced atomically; never expose a half-written file.
        for temporary, entry in staged:
            temporary.replace(destination / entry["filename"])
        entries = [entry for _, entry in prepared]
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=static, delete=False
        ) as stream:
            temporary_manifest = Path(stream.name)
            stream.write(json.dumps(entries, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            temporary_manifest.replace(static / "downloads.local.json")
        finally:
            temporary_manifest.unlink(missing_ok=True)
        return entries
    finally:
        if temporary_manifest is not None:
            temporary_manifest.unlink(missing_ok=True)
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mac", type=Path, help="Real macOS arm64 unsigned DMG")
    parser.add_argument("--windows", type=Path, help="Real Windows x64 unsigned EXE")
    parser.add_argument("--mac-evidence", type=Path, help="Matching Mac build sidecar")
    parser.add_argument(
        "--windows-evidence", type=Path, help="Matching Windows build sidecar"
    )
    args = parser.parse_args()
    if args.mac_evidence and not args.mac or args.windows_evidence and not args.windows:
        parser.error("An evidence sidecar requires its corresponding artifact")
    version = json.loads(
        (ROOT / "packages/markitdown-desktop/package.json").read_text()
    )["version"]
    inputs = [
        (file, platform, evidence)
        for file, platform, evidence in [
            (args.mac, "macos-arm64", args.mac_evidence),
            (args.windows, "windows-x64", args.windows_evidence),
        ]
        if file is not None
    ]
    try:
        entries = stage_artifacts(inputs, version)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(
        f"Staged {len(entries)} actual unsigned build(s): {STATIC / 'downloads.local.json'}"
    )
    print("Local website only. No upload, release, tag or production deployment.")


if __name__ == "__main__":
    main()
