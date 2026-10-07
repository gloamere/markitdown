"""CLI wiring uses only disposable synthetic accounts and offline files."""

import json
import os
import subprocess
import sys

from markitdown_web.auth import AuthService
from markitdown_web.jobs import JobService
from markitdown_web.state import Database, Settings


def command(*args):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("MARKITDOWN_")
    }
    env["ORT_DISABLE_TELEMETRY"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "markitdown_web", *map(str, args)],
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
    )


def test_offline_commands_roundtrip_and_required_confirmation(tmp_path):
    root = tmp_path / "data"
    settings = Settings(data_dir=root, start_workers=False)
    db = Database(settings)
    auth = AuthService(db, settings)
    admin = auth.bootstrap_admin("synthetic-admin", "synthetic-cli-123")
    jobs = JobService(db, settings)
    job = jobs.enqueue(
        admin["id"],
        [{"filename": "cli.txt", "suffix": ".txt", "data": b"synthetic cli"}],
    )[0]
    backup, journal, target = (
        tmp_path / "backup",
        tmp_path / "journal.json",
        tmp_path / "restored",
    )
    result = command("backup", "--data-dir", root, "--backup-dir", backup)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)
    jobs.delete_job(admin["id"], job["id"])
    result = command(
        "export-recovery-journal", "--data-dir", root, "--journal-file", journal
    )
    assert result.returncode == 0, result.stderr
    args = (
        "restore",
        "--backup-dir",
        backup,
        "--journal-file",
        journal,
        "--target-dir",
        target,
    )
    refused = command(*args)
    assert refused.returncode == 2
    assert not target.exists()
    restored = command(*args, "--confirm-latest-journal")
    assert restored.returncode == 0, restored.stderr
    with Database(Settings(data_dir=target)).connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_password_cli_refuses_noninteractive_real_entry(tmp_path):
    for action in ("bootstrap-admin", "reset-password"):
        response = command(action, "--data-dir", tmp_path / action)
        assert response.returncode == 2
        assert "交互式终端" in response.stderr
        assert not (tmp_path / action / "workspace.sqlite3").exists()


def test_cli_preflight_marks_local_boundary(tmp_path):
    result = command("preflight", "--data-dir", tmp_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["deployment_mode"] == "local"
    assert report["profiles"][0]["production_boundary"] is False
    assert "不构成生产隔离验收" in result.stderr
