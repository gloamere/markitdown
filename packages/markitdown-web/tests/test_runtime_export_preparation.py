"""Trusted CI image packaging, with no Docker or host namespace requirements."""
from __future__ import annotations

import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

from markitdown_web import sandbox

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/ci/prepare_runtime_export.py"
SPEC = importlib.util.spec_from_file_location("prepare_runtime_export", SCRIPT)
assert SPEC and SPEC.loader
export = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(export)
IMAGE = "sha256:" + "a" * 64


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    path = tmp_path / "markitdown-production.synthetic"
    path.mkdir(mode=0o700)
    return path


@pytest.fixture
def image(workspace):
    export.initialize(workspace, IMAGE)
    root = workspace / "runtime-root"
    for target in export.TARGETS:
        (root / target).mkdir()
    return root


def test_docker_init_layer_and_hidden_temporary_entries_are_prepared(
    workspace, image, capsys
):
    (image / "dev/pts").mkdir()
    (image / "dev/shm").mkdir()
    (image / "dev/console").touch()
    (image / "tmp/.import-cache").mkdir()
    (image / "tmp/.import-cache/private-name").write_text("private-content")
    (image / "proc/stale-build-artifact").touch()
    keep = image / "usr/lib/package"
    keep.parent.mkdir(parents=True)
    keep.write_text("runtime-package")
    report = export.prepare(workspace, IMAGE)
    assert report["before"]["dev"] == {"type": "directory", "entries": 3}
    assert report["before"]["tmp"]["entries"] == 1
    assert report["before"]["proc"]["entries"] == 1
    assert report["source_image_id"] == IMAGE
    assert report["preparation_version"] == export.VERSION
    assert report["status"] == "prepared"
    assert keep.read_text() == "runtime-package"
    for target in export.TARGETS:
        assert not list((image / target).iterdir())
        assert stat.S_IMODE((image / target).stat().st_mode) == (
            0o1777 if target == "tmp" else 0o755
        )
        assert report["after"][target] == {"type": "directory", "entries": 0}
    manifest = json.loads((image / export.MANIFEST).read_text())
    assert manifest["image_id"] == IMAGE
    assert manifest["preparation_version"] == export.VERSION
    assert not (workspace / export.MARKER).exists()
    diagnostic = capsys.readouterr().out + (workspace / export.REPORT).read_text()
    for secret in (
        "private-name",
        "private-content",
        ".import-cache",
        "stale-build-artifact",
    ):
        assert secret not in diagnostic


def test_missing_scaffolds_are_created(workspace):
    export.initialize(workspace, IMAGE)
    report = export.prepare(workspace, IMAGE)
    assert all(state["type"] == "missing" for state in report["before"].values())
    assert all(state["entries"] == 0 for state in report["after"].values())


@pytest.mark.parametrize("target", export.APPLICATION_TARGETS)
def test_application_state_is_refused_before_any_cleanup(workspace, image, target):
    sensitive = image / target / "unexpected-user-document"
    sensitive.write_text("synthetic-user-state")
    temporary = image / "tmp/build-cache"
    temporary.touch()
    with pytest.raises(
        export.PreparationError, match="unexpected-application-target-state"
    ):
        export.prepare(workspace, IMAGE)
    assert sensitive.read_text() == "synthetic-user-state"
    assert temporary.exists()
    assert not (image / export.MANIFEST).exists()
    report = json.loads((workspace / export.REPORT).read_text())
    assert report["before"][target]["entries"] == 1
    assert report["status"] == "rejected"


@pytest.mark.parametrize("target", export.TARGETS)
@pytest.mark.parametrize("kind", ("symlink", "file"))
def test_invalid_top_level_targets_are_never_repaired(
    workspace, image, tmp_path, target, kind
):
    node = image / target
    node.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep"
    sentinel.write_text("synthetic-outside-state")
    if kind == "symlink":
        node.symlink_to(outside, target_is_directory=True)
    else:
        node.write_text("unexpected-file")
    with pytest.raises(export.PreparationError, match="target-is-not-a-real-directory"):
        export.prepare(workspace, IMAGE)
    assert sentinel.read_text() == "synthetic-outside-state"
    assert not (image / export.MANIFEST).exists()


def test_ephemeral_nested_links_are_unlinked_without_following(
    workspace, image, tmp_path
):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep"
    sentinel.write_text("synthetic-outside-state")
    (image / "tmp/link").symlink_to(outside, target_is_directory=True)
    (image / "dev/nested").mkdir()
    (image / "dev/nested/link").symlink_to(sentinel)
    os.link(sentinel, image / "tmp/hard-link")
    export.prepare(workspace, IMAGE)
    assert sentinel.read_text() == "synthetic-outside-state"
    assert sentinel.stat().st_nlink == 1


@pytest.mark.parametrize("path", ("/", "/usr", "/tmp", "relative", "/tmp/../"))
def test_host_and_relative_roots_are_refused(workspace, path):
    with pytest.raises(export.PreparationError, match="not-a-dedicated-ci-workspace"):
        export.initialize(Path(path), IMAGE)


def test_symlinked_workspace_ancestor_is_refused(workspace, tmp_path, monkeypatch):
    link = tmp_path / "alias"
    link.symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setenv("RUNNER_TEMP", str(link))
    with pytest.raises(OSError):
        export.initialize(link / workspace.name, IMAGE)
    assert not (workspace / "runtime-root").exists()


def test_symlinked_root_is_refused(workspace, image, tmp_path):
    moved = tmp_path / "saved-export"
    image.rename(moved)
    image.symlink_to(moved, target_is_directory=True)
    with pytest.raises(OSError):
        export.prepare(workspace, IMAGE)
    assert not (moved / export.MANIFEST).exists()


def test_existing_directory_cannot_be_adopted(workspace):
    (workspace / "runtime-root").mkdir()
    with pytest.raises(FileExistsError):
        export.initialize(workspace, IMAGE)
    with pytest.raises(FileNotFoundError):
        export.prepare(workspace, IMAGE)


def test_source_identity_must_match_initialization(workspace, image):
    with pytest.raises(export.PreparationError, match="fresh-export-identity-mismatch"):
        export.prepare(workspace, "sha256:" + "b" * 64)
    assert not (image / export.MANIFEST).exists()


def test_replaced_export_directory_is_refused(workspace, image, tmp_path):
    image.rename(tmp_path / "original-export")
    image.mkdir()
    with pytest.raises(export.PreparationError, match="fresh-export-identity-mismatch"):
        export.prepare(workspace, IMAGE)
    assert not list(image.iterdir())


def test_symlinked_marker_is_refused(workspace, image, tmp_path):
    marker = workspace / export.MARKER
    original = tmp_path / "original-marker"
    marker.rename(original)
    marker.symlink_to(original)
    with pytest.raises(OSError):
        export.prepare(workspace, IMAGE)
    assert not (image / export.MANIFEST).exists()


def test_nonprivate_workspace_is_refused(workspace):
    workspace.chmod(0o755)
    with pytest.raises(export.PreparationError, match="ci-workspace-must-be-private"):
        export.initialize(workspace, IMAGE)
    assert not (workspace / "runtime-root").exists()


@pytest.mark.parametrize("identity", ("latest", "sha256:bad", "sha256:" + "A" * 64))
def test_noncanonical_image_identity_is_refused(workspace, identity):
    with pytest.raises(export.PreparationError, match="invalid-source-image-id"):
        export.initialize(workspace, identity)
    assert not (workspace / "runtime-root").exists()


def test_already_manifested_image_is_refused(workspace, image):
    (image / export.MANIFEST).write_text("existing-runtime")
    with pytest.raises(
        export.PreparationError, match="export-already-has-a-runtime-manifest"
    ):
        export.prepare(workspace, IMAGE)
    assert (image / export.MANIFEST).read_text() == "existing-runtime"


def test_preparation_is_one_shot(workspace, image):
    export.prepare(workspace, IMAGE)
    (image / "tmp/state-after-preparation").write_text("do-not-delete")
    with pytest.raises(FileNotFoundError):
        export.prepare(workspace, IMAGE)
    assert (image / "tmp/state-after-preparation").read_text() == "do-not-delete"


def test_report_symlink_is_never_followed(workspace, image, tmp_path):
    sentinel = tmp_path / "keep"
    sentinel.write_text("do-not-truncate")
    (workspace / export.REPORT).symlink_to(sentinel)
    with pytest.raises(FileExistsError):
        export.prepare(workspace, IMAGE)
    assert sentinel.read_text() == "do-not-truncate"


def test_prepared_image_passes_existing_layout_guard(
    workspace, image, tmp_path, monkeypatch
):
    from types import SimpleNamespace

    tmp_path = tmp_path.resolve()
    # This is a path/layout fixture, not a kernel isolation claim on non-Linux.
    monkeypatch.setattr(sandbox, "sys", SimpleNamespace(platform="linux"))
    python = image / "usr/bin/python3"
    python.parent.mkdir(parents=True)
    python.write_text("synthetic fixture, never executed")
    python.chmod(0o755)
    launcher = tmp_path / "bwrap"
    launcher.write_text("synthetic fixture, never executed")
    launcher.chmod(0o755)
    export.prepare(workspace, IMAGE)
    settings = SimpleNamespace(
        sandbox_runtime_root=image,
        sandbox_bwrap=launcher,
        data_dir=tmp_path / "unrelated-service-data",
    )
    assert sandbox.runtime_paths(settings, "markitdown") == (
        image,
        launcher,
        "/usr/bin/python3",
    )
    (image / "dev/unexpected-runtime-state").touch()
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.runtime_paths(settings, "markitdown")
