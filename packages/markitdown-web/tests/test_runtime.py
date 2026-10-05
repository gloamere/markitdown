"""Startup, process lifecycle, and session-identity integration regressions."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


def test_help_and_noninteractive_bootstrap_never_create_accounts(tmp_path):
    environment = {
        **os.environ,
        "ORT_DISABLE_TELEMETRY": "1",
        "MARKITDOWN_DATA_DIR": str(tmp_path / "private"),
    }
    help_result = subprocess.run(
        [sys.executable, "-m", "markitdown_web", "--help"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert help_result.returncode == 0
    assert "bootstrap-admin" in help_result.stdout
    result = subprocess.run(
        [sys.executable, "-m", "markitdown_web", "bootstrap-admin"],
        cwd=tmp_path,
        env=environment,
        input="",
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert not (tmp_path / "private").exists()


def test_authenticated_identity_header_matches_session(client):
    response = client.get("/api/me")
    assert response.status_code == 200
    user_id = response.json()["user"]["id"]
    assert response.headers["x-markitdown-user"] == user_id
    assert client.get("/api/jobs").headers["x-markitdown-user"] == user_id
    response = client.post("/api/auth/logout", headers={"X-MarkItDown-Request": "1"})
    assert response.status_code == 403
    assert response.headers["x-markitdown-user"] == user_id


@pytest.mark.skipif(
    sys.platform != "linux", reason="Linux parent-death lifecycle control"
)
def test_parser_is_killed_when_service_parent_exits(tmp_path):
    ready = tmp_path / "ready"
    child_program = (
        "import os,time; from pathlib import Path; "
        "from markitdown_web.worker import apply_limits; apply_limits(); "
        f"Path({str(ready)!r}).write_text(str(os.getpid())); time.sleep(30)"
    )
    parent_program = (
        "import os,subprocess,sys; "
        "env=dict(os.environ,MARKITDOWN_PARENT_PID=str(os.getpid())); "
        f"child=subprocess.Popen([sys.executable,'-c',{child_program!r}],env=env); child.wait()"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_program],
        cwd=tmp_path,
        env={**os.environ, "ORT_DISABLE_TELEMETRY": "1"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "Synthetic parser did not initialize"
        child_pid = int(ready.read_text())
        parent.terminate()
        parent.wait(timeout=5)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            stat = Path(f"/proc/{child_pid}/stat")
            try:
                process_state = stat.read_text().rsplit(") ", 1)[1].split()[0]
            except FileNotFoundError:
                break
            if process_state == "Z":
                break
            time.sleep(0.02)
        else:
            pytest.fail("Parser survived its service parent")
    finally:
        # Only our synthetic test's dedicated process group is affected.
        try:
            os.killpg(parent.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        parent.wait(timeout=5)
