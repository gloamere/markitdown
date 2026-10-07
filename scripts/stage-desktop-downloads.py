"""Stage unsigned desktop builds for a local website; never publish a release."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "packages/markitdown-web/src/markitdown_web/static"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mac", type=Path, help="Real macOS arm64 unsigned DMG")
    parser.add_argument("--windows", type=Path, help="Real Windows x64 unsigned EXE")
    args = parser.parse_args()
    if not args.mac and not args.windows:
        parser.error("Provide at least one actual build artifact")
    version = json.loads(
        (ROOT / "packages/markitdown-desktop/package.json").read_text()
    )["version"]
    destination = STATIC / "downloads"
    destination.mkdir(exist_ok=True)
    entries = []
    for file, platform, suffix, os_name, arch in [
        (args.mac, "macos-arm64", ".dmg", "mac", "arm64"),
        (args.windows, "windows-x64", ".exe", "win", "x64"),
    ]:
        if file is None:
            continue
        expected = f"MarkItDown-{version}-{os_name}-{arch}-unsigned{suffix}"
        if file.is_symlink() or not file.is_file() or file.name != expected:
            parser.error(f"Expected a real, non-symlink artifact named {expected}")
        target = destination / expected
        shutil.copyfile(file, target)
        with target.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        entries.append(
            {
                "platform": platform,
                "filename": expected,
                "version": version,
                "bytes": target.stat().st_size,
                "sha256": digest,
            }
        )
    manifest = STATIC / "downloads.local.json"
    manifest.write_text(json.dumps(entries, indent=2) + "\n")
    print(f"Staged {len(entries)} actual unsigned build(s): {manifest}")
    print("Local website only. No upload, release, tag or production deployment.")


if __name__ == "__main__":
    main()
