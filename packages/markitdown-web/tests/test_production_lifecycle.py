"""Three bounded positive lifecycle gates for the opt-in production CI runtime.

Only the Python payload is substituted: the real command, worker limits/filter,
Bubblewrap wrapper tree, conversion supervisor and scheduler run unchanged.
Cancellation/timeout pause immediately before real termination to observe slot
retention deterministically; no launch, wait, signal or reap result is mocked.
Parent death kills a separate real supervisor, without calling its cleanup path.

These gates establish finite synthetic lifecycle behavior, not D-state recovery,
Docling/model behavior, capacity, target-host acceptance or parser quality.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from test_job_service import service as service
from test_job_service import upload

from markitdown_web import conversion, sandbox

READY_SECONDS = 5
CLEANUP_SECONDS = 5
PAYLOAD_SECONDS = 30
# Keep this executable fixture finite even if a lifecycle assertion fails. It
# applies the actual worker resource limits and seccomp filter before readiness.
PAYLOAD = """import sys;sys.path.insert(0,'/code')
from markitdown_web.worker import apply_limits
from pathlib import Path
import json,os,signal,time
apply_limits()
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path('/output/result.json').write_text(json.dumps({
    'phase':'ready','token':TOKEN,'pid':os.getpid(),
    'namespace':os.readlink('/proc/self/ns/pid')}))
time.sleep(DURATION)
Path('/output/result.json').write_text(json.dumps({
    'phase':'finished','markdown':'SYNTHETIC LATE OUTPUT'}))
"""
SUPERVISOR = """import json,sys
from pathlib import Path
from markitdown_web.conversion import _supervise
config=json.loads(Path(sys.argv[1]).read_text())
_supervise(config['argv'],config['env'],Path(config['directory']),
           lambda:False,timeout=25)
"""


def _payload(token):
    return PAYLOAD.replace("TOKEN", repr(token)).replace(
        "DURATION", str(PAYLOAD_SECONDS)
    )


def _wait_for(predicate, seconds=READY_SECONDS):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.02)
    pytest.fail("Production lifecycle condition did not complete within its bound")


def _ready(output, token):
    try:
        result = json.loads(output.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    assert result.get("phase") == "ready", "Finite payload completed unexpectedly"
    assert result.get("token") == token
    return result


def _identity(pid):
    """Observe PID plus kernel start time, avoiding PID-reuse false positives."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        # procfs reports ENOENT or ESRCH if exit races the open/read operation.
        return None
    fields = raw[raw.rfind(")") + 2 :].split()
    return {
        "pid": pid,
        "state": fields[0],
        "parent": int(fields[1]),
        "group": int(fields[2]),
        "start": int(fields[19]),
    }


def _alive(identity):
    current = _identity(identity["pid"])
    return (
        current is not None
        and current["start"] == identity["start"]
        and current["state"] not in {"Z", "X"}
    )


@pytest.mark.parametrize("failure", [FileNotFoundError, ProcessLookupError])
def test_lifecycle_identity_handles_confirmed_process_disappearance(
    monkeypatch, failure
):
    def read(_path):
        raise failure("Synthetic exited process")

    monkeypatch.setattr(Path, "read_text", read)
    assert _identity(123) is None


def test_lifecycle_identity_does_not_hide_permission_errors(monkeypatch):
    def read(_path):
        raise PermissionError("Synthetic observation denial")

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(PermissionError):
        _identity(123)


def _tree(root, settings, token, ready):
    """Require a real wrapper plus its identified, live namespace worker."""
    pending, observed = [root], {}
    worker = None
    while pending:
        pid = pending.pop()
        if pid in observed:
            continue
        identity = _identity(pid)
        assert identity is not None and identity["state"] not in {"Z", "X"}
        observed[pid] = identity
        children = Path(f"/proc/{pid}/task/{pid}/children").read_text()
        pending.extend(int(child) for child in children.split())
        command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        if command[0] == settings.sandbox_python.encode():
            assert token.encode() in b"\0".join(command)
            status = Path(f"/proc/{pid}/status").read_text().splitlines()
            namespace_pids = next(line for line in status if line.startswith("NSpid:"))
            assert int(namespace_pids.split()[-1]) == ready["pid"]
            assert ready["namespace"] != os.readlink("/proc/self/ns/pid")
            worker = pid
    assert len(observed) >= 2 and worker is not None and worker != root
    assert observed[root]["group"] == root
    assert all(_alive(identity) for identity in observed.values())
    return observed


def _assert_dead(observed):
    # An orphan zombie waiting for host init is dead, cannot write and has no
    # resident resources. Only the direct supervisor child can be reaped here.
    _wait_for(
        lambda: all(not _alive(identity) for identity in observed.values()),
        CLEANUP_SECONDS,
    )


def _assert_reaped(process, observed):
    assert process.returncode is not None
    with pytest.raises(ChildProcessError):
        os.waitpid(process.pid, os.WNOHANG)
    _assert_dead(observed)
    assert not sandbox._group_alive(process.pid)


def _emergency_cleanup(process, observed):
    """Bounded failure cleanup, never used as evidence for a passing assertion."""
    for identity in observed.values():
        if _alive(identity):
            try:
                os.kill(identity["pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass
    if process is not None:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=CLEANUP_SECONDS)


@pytest.fixture
def production_service(service):
    root = os.environ.get("MARKITDOWN_TEST_RUNTIME_ROOT")
    if not root:
        pytest.skip("Requires the explicitly provisioned production CI runtime")
    # Once opted in, an unsupported platform, unreadable /proc, denied namespace,
    # broken runtime or launcher is a failure. There is no fallback or skip.
    assert sys.platform == "linux"
    service.settings = replace(
        service.settings,
        deployment_mode="production",
        cookie_secure=True,
        public_origin="https://synthetic.invalid",
        sandbox_runtime_root=Path(root),
        sandbox_bwrap=Path(
            os.environ.get("MARKITDOWN_SANDBOX_BWRAP", "/usr/bin/bwrap")
        ),
        sandbox_python=os.environ.get(
            "MARKITDOWN_TEST_SANDBOX_PYTHON", "/usr/bin/python3"
        ),
        global_concurrency=1,
        per_user_concurrency=1,
    )
    sandbox.runtime_paths(service.settings, "markitdown")
    return service


def _scheduler_lifecycle(service, monkeypatch, *, cancel):
    # Admission first runs the unmodified production readiness probe.
    first = service.enqueue("alice", [upload()], submission_key="first")[0]
    second = service.enqueue("alice", [upload()], submission_key="second")[0]
    row = service._claim()
    assert row is not None and row["id"] == first["id"]
    token = "synthetic-lifecycle-" + uuid.uuid4().hex
    original_command, original_popen = sandbox.command, subprocess.Popen
    original_terminate = sandbox.terminate
    observed_processes, outputs, barrier_timeouts, errors = [], [], [], []
    cleanup_evidence = []
    entered, release = threading.Event(), threading.Event()

    def command(*args, **kwargs):
        argv, env = original_command(*args, **kwargs)
        assert kwargs["engine"] == "markitdown" and kwargs["mode"] == "convert"
        argv[argv.index("-c") + 1] = _payload(token)
        outputs.append(kwargs["output"])
        return argv, env

    def launch(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        observed_processes.append(process)
        return process

    def paused_terminate(process):
        # Observe the real live child at the cleanup boundary; this does not
        # implement, bypass or fake any part of actual production termination.
        entered.set()
        try:
            if not release.wait(READY_SECONDS):
                barrier_timeouts.append(True)
        finally:
            original_terminate(process)
            # Observe immediately: waiting here would hide premature cleanup
            # return and artificially retain the scheduler's physical slot.
            cleanup_evidence.append(
                (
                    process.returncode is not None,
                    all(not _alive(x) for x in observed.values()),
                    not sandbox._group_alive(process.pid),
                    time.time(),
                )
            )

    def execute():
        try:
            service._execute(row)
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(sandbox, "command", command)
    monkeypatch.setattr(conversion.subprocess, "Popen", launch)
    monkeypatch.setattr(sandbox, "terminate", paused_terminate)
    if not cancel:
        # run_conversion uses this deadline; the real _supervise loop enforces it.
        monkeypatch.setattr(conversion, "CONVERSION_TIMEOUT", READY_SECONDS + 1)
    thread = threading.Thread(target=execute, daemon=True)
    observed = {}
    thread.start()
    try:
        ready = _wait_for(lambda: outputs and _ready(outputs[0], token))
        assert len(observed_processes) == 1
        process = observed_processes[0]
        observed = _tree(process.pid, service.settings, token, ready)
        if cancel:
            result = service.cancel("alice", first["id"])
            assert result["lifecycle_status"] == "stopping"
        assert entered.wait(READY_SECONDS + 2)
        assert process.poll() is None and all(_alive(x) for x in observed.values())
        current = service.get_job("alice", first["id"])
        assert current["attempt_history"][0]["physical_released_at"] is None
        assert first["id"] in service._inflight
        assert service._claim() is None
        assert service.get_job("alice", second["id"])["status"] == "queued"
        assert outputs[0].parent in sandbox.active_workspaces()
        assert not (service.jobs_dir / first["id"] / "markdown.md").exists()
        release.set()
        thread.join(CLEANUP_SECONDS)
        assert not thread.is_alive() and not errors and not barrier_timeouts
        _assert_reaped(process, observed)
        assert len(cleanup_evidence) == 1
        reaped, tree_dead, group_dead, reaped_at = cleanup_evidence[0]
        assert reaped and tree_dead and group_dead
        final = service.get_job("alice", first["id"])
        attempt = final["attempt_history"][0]
        assert final["lifecycle_status"] == ("cancelled" if cancel else "failed")
        assert ("已取消" if cancel else "超时") in final["error"]
        assert attempt["physical_released_at"] >= attempt["finished_at"]
        assert attempt["physical_released_at"] >= reaped_at
        assert final["error_code"] == ("cancelled" if cancel else "timeout")
        assert first["id"] not in service._inflight
        assert not outputs[0].parent.exists()
        assert outputs[0].parent not in sandbox.active_workspaces()
        assert not (service.jobs_dir / first["id"] / "markdown.md").exists()
        assert not (service.jobs_dir / first["id"] / "preview.html").exists()
        assert not (service.jobs_dir / first["id"] / "result.json").exists()
        # Re-admission proves the physical slot is usable only after real reap.
        next_row = service._claim()
        assert next_row is not None and next_row["id"] == second["id"]
        service.cancel("alice", second["id"])
        service._execute(next_row)
        assert not service._inflight
        assert len(observed_processes) == 1
    finally:
        release.set()
        for process in observed_processes:
            _emergency_cleanup(process, observed)
        thread.join(CLEANUP_SECONDS)


def test_production_cancellation_reaps_tree_before_slot_release(
    production_service, monkeypatch
):
    _scheduler_lifecycle(production_service, monkeypatch, cancel=True)


def test_production_timeout_reaps_tree_before_slot_release(
    production_service, monkeypatch
):
    _scheduler_lifecycle(production_service, monkeypatch, cancel=False)


def test_production_parent_death_kills_actual_wrapper_tree(
    production_service, tmp_path
):
    settings = production_service.settings
    source, output = tmp_path / "source.txt", tmp_path / "result.json"
    source.write_text("Synthetic finite lifecycle fixture")
    output.touch(mode=0o600)
    token = "synthetic-parent-death-" + uuid.uuid4().hex
    argv, env = sandbox.command(
        settings,
        engine="markitdown",
        mode="convert",
        source=source,
        output=output,
        suffix=".txt",
    )
    argv[argv.index("-c") + 1] = _payload(token)
    config = tmp_path / "supervisor.json"
    config.write_text(
        json.dumps({"argv": argv, "env": env, "directory": str(tmp_path)})
    )
    parent = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", SUPERVISOR, str(config)],
        env=sandbox.environment(tmp_path),
        cwd=tmp_path,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    observed = {}
    try:
        ready = _wait_for(lambda: _ready(output, token))
        children = (
            Path(f"/proc/{parent.pid}/task/{parent.pid}/children").read_text().split()
        )
        assert len(children) == 1
        wrapper = int(children[0])
        observed = _tree(wrapper, settings, token, ready)
        assert observed[wrapper]["parent"] == parent.pid
        assert observed[wrapper]["group"] != os.getpgid(parent.pid)
        # Kill only the supervisor, not its group or any Bubblewrap process.
        # SIGKILL prevents Python finally/sandbox.terminate from helping.
        parent.kill()
        parent.wait(timeout=CLEANUP_SECONDS)
        assert parent.returncode == -signal.SIGKILL
        with pytest.raises(ChildProcessError):
            os.waitpid(parent.pid, os.WNOHANG)
        _assert_dead(observed)
        assert not sandbox._group_alive(wrapper)
        assert _ready(output, token) == ready
    finally:
        _emergency_cleanup(parent, observed)


def test_lifecycle_fixture_scripts_compile_and_are_bounded():
    # Ordinary local self-check only: this never launches a sandbox or filter.
    compile(_payload("synthetic-selfcheck"), "<lifecycle-payload>", "exec")
    compile(SUPERVISOR, "<lifecycle-supervisor>", "exec")
    assert READY_SECONDS + CLEANUP_SECONDS < PAYLOAD_SECONDS
    assert "time.sleep(30)" in _payload("synthetic-selfcheck")
