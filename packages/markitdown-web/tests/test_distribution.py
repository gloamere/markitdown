"""A download entry is visible only when a matching local artifact exists."""

import hashlib
import json
import os

import pytest
from fastapi.testclient import TestClient

from markitdown_web.app import create_app
from markitdown_web.distribution import downloads
from markitdown_web.state import Settings


def test_public_site_and_authenticated_workspace_have_separate_csp(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, start_workers=False))
    with TestClient(app, base_url="http://localhost") as client:
        website = client.get("/")
        workspace = client.get("/app")
        assert website.status_code == workspace.status_code == 200
        assert "获取桌面客户端" in website.text
        assert 'id="login-form"' in workspace.text
        assert "img-src 'self'" in website.headers["content-security-policy"]
        assert "img-src 'none'" in workspace.headers["content-security-policy"]
        assert client.get("/api/jobs").status_code == 401
        assert client.get("/api/downloads").status_code == 200


def test_missing_or_corrupt_manifest_has_no_fake_download(tmp_path):
    assert downloads(tmp_path) == {"downloads": []}
    (tmp_path / "downloads.local.json").write_text("bad json")
    assert downloads(tmp_path) == {"downloads": []}


def test_only_real_bounded_local_download_entries_are_exposed(tmp_path):
    directory = tmp_path / "downloads"
    directory.mkdir()
    name = "MarkItDown-1.1.0-dev.1-mac-arm64-unsigned.dmg"
    (directory / name).write_bytes(b"synthetic-artifact")
    entry = {
        "filename": name,
        "bytes": 18,
        "sha256": hashlib.sha256(b"synthetic-artifact").hexdigest(),
        "platform": "macos-arm64",
        "version": "1.1.0-dev.1",
    }
    manifest = tmp_path / "downloads.local.json"
    manifest.write_text(json.dumps([entry]))
    assert downloads(tmp_path)["downloads"][0]["signed"] is False
    assert downloads(tmp_path)["downloads"][0]["url"] == f"/static/downloads/{name}"
    for changed in [
        {"filename": "../../secret.dmg"},
        {"bytes": 999},
        {"platform": "macos-x64"},
        {"platform": []},
        {"platform": {}},
        {"sha256": "invalid"},
        {"sha256": "a" * 64},
        {"version": "1.2.0"},
        {"platform": "windows-x64"},
        {"version": "x" * 100},
    ]:
        manifest.write_text(json.dumps([{**entry, **changed}]))
        assert downloads(tmp_path) == {"downloads": []}
    (directory / name).unlink()
    (directory / name).symlink_to(manifest)
    manifest.write_text(json.dumps([entry]))
    assert downloads(tmp_path) == {"downloads": []}


def synthetic_download(root, payload=b"synthetic-artifact"):
    directory = root / "downloads"
    directory.mkdir()
    file = directory / "MarkItDown-1.1.0-dev.1-mac-arm64-unsigned.dmg"
    file.write_bytes(payload)
    entry = {
        "filename": file.name,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "platform": "macos-arm64",
        "version": "1.1.0-dev.1",
    }
    manifest = root / "downloads.local.json"
    manifest.write_text(json.dumps([entry]))
    return file, manifest, entry


def test_same_size_corruption_and_replacement_invalidate_cached_hash(tmp_path):
    file, manifest, entry = synthetic_download(tmp_path)
    assert len(downloads(tmp_path)["downloads"]) == 1
    old = file.stat()
    file.write_bytes(b"x" * old.st_size)
    os.utime(file, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert downloads(tmp_path) == {"downloads": []}
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"y" * old.st_size)
    os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
    replacement.replace(file)
    assert downloads(tmp_path) == {"downloads": []}
    manifest.write_text(
        json.dumps([{**entry, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}])
    )
    assert len(downloads(tmp_path)["downloads"]) == 1


def test_unchanged_artifact_is_hashed_only_once(tmp_path, monkeypatch):
    import markitdown_web.distribution as module

    synthetic_download(tmp_path)
    real = module.hashlib.file_digest
    calls = []

    def tracked(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(module.hashlib, "file_digest", tracked)
    assert downloads(tmp_path) == downloads(tmp_path)
    assert len(calls) == (2 if os.name == "nt" else 1)


@pytest.mark.parametrize(
    "change",
    [
        "manifest_symlink",
        "directory_symlink",
        "oversized_manifest",
        "empty_artifact",
        "boolean_size",
    ],
)
def test_invalid_staging_inputs_are_not_advertised(tmp_path, change):
    file, manifest, entry = synthetic_download(tmp_path)
    if change == "manifest_symlink":
        original = tmp_path / "original.json"
        manifest.rename(original)
        manifest.symlink_to(original)
    elif change == "directory_symlink":
        original = tmp_path / "original"
        file.parent.rename(original)
        (tmp_path / "downloads").symlink_to(original, target_is_directory=True)
    elif change == "oversized_manifest":
        manifest.write_text(" " * (64 * 1024 + 1) + json.dumps([entry]))
    elif change == "empty_artifact":
        file.write_bytes(b"")
        manifest.write_text(
            json.dumps(
                [{**entry, "bytes": 0, "sha256": hashlib.sha256(b"").hexdigest()}]
            )
        )
    else:
        file.write_bytes(b"x")
        manifest.write_text(
            json.dumps(
                [{**entry, "bytes": True, "sha256": hashlib.sha256(b"x").hexdigest()}]
            )
        )
    assert downloads(tmp_path) == {"downloads": []}


def test_file_changed_while_hashing_is_not_advertised(tmp_path, monkeypatch):
    import markitdown_web.distribution as module

    file, _, _ = synthetic_download(tmp_path)
    original = module.hashlib.file_digest

    def mutate(stream, algorithm):
        result = original(stream, algorithm)
        file.write_bytes(b"changed" * 3)
        return result

    monkeypatch.setattr(module.hashlib, "file_digest", mutate)
    assert downloads(tmp_path) == {"downloads": []}


def test_source_commit_is_optional_but_must_be_valid_if_present(tmp_path):
    _, manifest, entry = synthetic_download(tmp_path)
    assert "source_commit" not in downloads(tmp_path)["downloads"][0]
    manifest.write_text(json.dumps([{**entry, "source_commit": "c" * 40}]))
    assert downloads(tmp_path)["downloads"][0]["source_commit"] == "c" * 40
    manifest.write_text(json.dumps([{**entry, "source_commit": "main"}]))
    assert downloads(tmp_path) == {"downloads": []}


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named pipes only")
def test_nonregular_manifest_and_artifact_are_rejected_without_opening(tmp_path):
    file, manifest, _ = synthetic_download(tmp_path)
    file.unlink()
    os.mkfifo(file)
    assert downloads(tmp_path) == {"downloads": []}
    manifest.unlink()
    os.mkfifo(manifest)
    assert downloads(tmp_path) == {"downloads": []}
