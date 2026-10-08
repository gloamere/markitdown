"""Synthetic local installer staging, evidence matching and atomic publication."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from markitdown_web.distribution import downloads

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/stage-desktop-downloads.py"
spec = importlib.util.spec_from_file_location("desktop_staging", SCRIPT)
assert spec and spec.loader
staging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(staging)
VERSION = "1.1.0-dev.1"


def inputs(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    artifact = source / f"MarkItDown-{VERSION}-mac-arm64-unsigned.dmg"
    artifact.write_bytes(b"synthetic installer bytes")
    report = {
        "schema_version": 1,
        "kind": "markitdown_desktop_build_evidence",
        "source": {"commit": "c" * 40, "tree": "d" * 40, "clean": True},
        "artifact": {
            "filename": artifact.name,
            "version": VERSION,
            "platform": "macos",
            "arch": "arm64",
            "bytes": artifact.stat().st_size,
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        },
    }
    evidence = source / "build-evidence.json"
    evidence.write_text(json.dumps(report))
    static = tmp_path / "static"
    static.mkdir()
    return artifact, evidence, report, static


def test_staging_verifies_sidecar_and_exposes_only_bound_source_commit(tmp_path):
    artifact, evidence, _, static = inputs(tmp_path)
    entries = staging.stage_artifacts(
        [(artifact, "macos-arm64", evidence)], VERSION, static
    )
    assert entries[0]["source_commit"] == "c" * 40
    assert downloads(static)["downloads"][0]["source_commit"] == "c" * 40
    assert (static / "downloads" / artifact.name).read_bytes() == artifact.read_bytes()
    assert sorted(path.name for path in static.iterdir()) == [
        "downloads",
        "downloads.local.json",
    ]
    assert sorted(path.name for path in (static / "downloads").iterdir()) == [
        artifact.name
    ]


def test_legacy_staging_does_not_invent_a_source_commit(tmp_path):
    artifact, _, _, static = inputs(tmp_path)
    staging.stage_artifacts([(artifact, "macos-arm64", None)], VERSION, static)
    assert "source_commit" not in downloads(static)["downloads"][0]


@pytest.mark.parametrize(
    "field",
    ["filename", "version", "sha256", "bytes", "platform", "arch", "commit", "clean"],
)
def test_mismatched_evidence_cannot_replace_existing_manifest(tmp_path, field):
    artifact, evidence, report, static = inputs(tmp_path)
    manifest = static / "downloads.local.json"
    manifest.write_text("previous manifest")
    if field in {"commit", "clean"}:
        report["source"][field] = False if field == "clean" else "not-a-commit"
    else:
        report["artifact"][field] = "mismatch"
    evidence.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        staging.stage_artifacts([(artifact, "macos-arm64", evidence)], VERSION, static)
    assert manifest.read_text() == "previous manifest"
    assert not (static / "downloads").exists()


def test_all_inputs_are_validated_before_copying_any(tmp_path):
    artifact, _, _, static = inputs(tmp_path)
    missing = tmp_path / f"MarkItDown-{VERSION}-win-x64-unsigned.exe"
    with pytest.raises(ValueError):
        staging.stage_artifacts(
            [(artifact, "macos-arm64", None), (missing, "windows-x64", None)],
            VERSION,
            static,
        )
    assert list(static.iterdir()) == []


def test_copy_failure_preserves_previous_manifest_and_leaves_no_temp(
    tmp_path, monkeypatch
):
    artifact, _, _, static = inputs(tmp_path)
    manifest = static / "downloads.local.json"
    manifest.write_text("previous manifest")

    def fail(source, target):
        target.write(b"partial")
        raise OSError("Synthetic copy failure")

    monkeypatch.setattr(staging.shutil, "copyfileobj", fail)
    with pytest.raises(OSError):
        staging.stage_artifacts([(artifact, "macos-arm64", None)], VERSION, static)
    assert manifest.read_text() == "previous manifest"
    assert list((static / "downloads").iterdir()) == []


def test_manifest_write_failure_preserves_old_manifest_and_cleans_temp(
    tmp_path, monkeypatch
):
    artifact, _, _, static = inputs(tmp_path)
    manifest = static / "downloads.local.json"
    manifest.write_text("previous manifest")
    original = staging.os.fsync
    calls = []

    def fail_manifest(fd):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("Synthetic manifest write failure")
        return original(fd)

    monkeypatch.setattr(staging.os, "fsync", fail_manifest)
    with pytest.raises(OSError):
        staging.stage_artifacts([(artifact, "macos-arm64", None)], VERSION, static)
    assert manifest.read_text() == "previous manifest"
    assert sorted(path.name for path in static.iterdir()) == [
        "downloads",
        "downloads.local.json",
    ]
