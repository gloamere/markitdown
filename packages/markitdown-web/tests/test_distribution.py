"""A download entry is visible only when a matching local artifact exists."""

import json

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
        "sha256": "a" * 64,
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
        {"sha256": "invalid"},
        {"version": "x" * 100},
    ]:
        manifest.write_text(json.dumps([{**entry, **changed}]))
        assert downloads(tmp_path) == {"downloads": []}
    (directory / name).unlink()
    (directory / name).symlink_to(manifest)
    manifest.write_text(json.dumps([entry]))
    assert downloads(tmp_path) == {"downloads": []}
