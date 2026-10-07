"""Verify a real DMG, then test its extracted app with disposable service/data."""

import argparse
import hashlib
import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def command(arguments, timeout=90, check=True, env=None):
    result = subprocess.run(
        arguments, capture_output=True, text=True, timeout=timeout, env=env, check=False
    )
    if check and result.returncode:
        raise RuntimeError(f"{Path(arguments[0]).name} failed: {result.stderr.strip()}")
    return result


def verify(dmg: Path, output: Path) -> None:
    if sys.platform != "darwin" or not dmg.is_file() or dmg.suffix != ".dmg":
        raise ValueError("Expected a real DMG on macOS")
    output.mkdir(parents=True, exist_ok=True)
    with dmg.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    report = {
        "artifact": dmg.name,
        "sha256": digest,
        "bytes": dmg.stat().st_size,
        "native_status": "pending",
        "signing_status": "pending",
        "gatekeeper_status": "pending",
        "notarization_status": "not_verified",
        "kind": "local_developer_build_dmg_acceptance",
    }
    evidence = output / "mac-package-evidence.json"
    try:
        with tempfile.TemporaryDirectory(prefix="markitdown-dmg-test-") as directory:
            staging = Path(directory).resolve()
            mount = staging / "volume"
            mount.mkdir()
            mounted = False
            try:
                command(
                    [
                        "hdiutil",
                        "attach",
                        "-readonly",
                        "-nobrowse",
                        "-mountpoint",
                        str(mount),
                        str(dmg),
                    ],
                    timeout=120,
                )
                mounted = True
                source = mount / "MarkItDown.app"
                if source.is_symlink() or not source.is_dir():
                    raise ValueError("DMG has no real MarkItDown.app")
                app = staging / "extracted/MarkItDown.app"
                app.parent.mkdir()
                command(["ditto", str(source), str(app)], timeout=120)
                command(["codesign", "--verify", "--deep", "--strict", str(app)])
                signature = command(
                    ["codesign", "--display", "--verbose=4", str(app)]
                ).stderr
                authority = re.search(
                    r"^Authority=(Developer ID Application: .+)$",
                    signature,
                    re.MULTILINE,
                )
                team = re.search(
                    r"^TeamIdentifier=([A-Z0-9]{10})$", signature, re.MULTILINE
                )
                if (
                    not authority
                    or not team
                    or "runtime" not in signature
                    or "Timestamp=" not in signature
                ):
                    raise RuntimeError(
                        "Developer ID, secure timestamp and hardened runtime are required"
                    )
                report.update(
                    {
                        "signing_status": "verified",
                        "authority": authority[1],
                        "team": team[1],
                        "hardened_runtime": True,
                        "secure_timestamp": True,
                    }
                )
                entitlements = command(
                    ["codesign", "--display", "--entitlements", ":-", str(app)]
                ).stdout
                granted = plistlib.loads(entitlements.encode())
                if granted != {"com.apple.security.cs.allow-jit": True}:
                    raise RuntimeError("Expected only the reviewed JIT entitlement")
                assessment = command(
                    ["spctl", "--assess", "--type", "execute", "--verbose=4", str(app)],
                    check=False,
                )
                report["gatekeeper_status"] = (
                    "accepted" if assessment.returncode == 0 else "rejected"
                )
                report["gatekeeper_detail"] = assessment.stderr.strip()
                quarantined = any(
                    "com.apple.quarantine"
                    in command(["xattr", str(file)]).stdout.splitlines()
                    for file in (app, dmg)
                )
                report["quarantined"] = quarantined
                # Never override a denied downloaded/quarantined application.
                if quarantined and assessment.returncode:
                    raise RuntimeError(
                        "Gatekeeper denied this quarantined app; native launch is blocked until notarization"
                    )
                environment = {
                    **os.environ,
                    "ORT_DISABLE_TELEMETRY": "1",
                    "MARKITDOWN_TEST_EXECUTABLE": str(
                        app / "Contents/MacOS/MarkItDown"
                    ),
                    "MARKITDOWN_TEST_EVIDENCE": str(output),
                    "MARKITDOWN_TEST_BACKGROUND": "1",
                }
                connection = command(
                    [
                        "node",
                        str(ROOT / "packages/markitdown-desktop/tests/setup-smoke.cjs"),
                    ],
                    timeout=90,
                    env=environment,
                )
                print(connection.stdout, end="", flush=True)
                service = command(
                    [
                        sys.executable,
                        str(ROOT / "scripts/verify-desktop-service.py"),
                        "--app",
                        str(app),
                        "--out",
                        str(output),
                    ],
                    timeout=240,
                    env=environment,
                )
                print(service.stdout, end="", flush=True)
                report["native_status"] = "passed"
                print(
                    "Actual DMG app passed signing, isolated connection and real-service flows. Gatekeeper:",
                    report["gatekeeper_status"],
                    flush=True,
                )
            finally:
                if mounted:
                    command(["hdiutil", "detach", str(mount)], timeout=60)
    finally:
        evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dmg", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    arguments = parser.parse_args()
    verify(arguments.dmg.resolve(), arguments.out.resolve())
