"""The Web release version is consistent without renaming upstream packages."""
from __future__ import annotations

import importlib.metadata
import tomllib
from pathlib import Path

from fastapi.testclient import TestClient

from markitdown_web import __version__
from markitdown_web.app import create_app
from markitdown_web.state import Settings


def test_web_release_version_matches_package_metadata():
    project = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(project.read_text())["project"]
    assert declared["name"] == "markitdown-web"
    assert declared["version"] == __version__ == "1.0.0"
    assert importlib.metadata.version("markitdown-web") == __version__


def test_application_reports_release_version_without_creating_accounts(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        deployment_mode="local",
        cookie_secure=False,
        public_origin=None,
        start_workers=False,
        docling_enabled=False,
    )
    app = create_app(settings)
    assert app.version == __version__
    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/api/config")
        assert response.status_code == 200
        assert response.json()["application_version"] == "1.0.0"
        assert response.json()["has_admin"] is False
