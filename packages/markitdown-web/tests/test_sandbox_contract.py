"""Production boundary contracts. Positive kernel isolation requires an approved image.

The opt-in image test is a deployment gate, not an automatic local-mode fallback.
Unavailable namespaces must stay unavailable; tests never change host controls.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from markitdown_web import conversion, engines, sandbox


@pytest.fixture
def image_settings(tmp_path, monkeypatch):
    # This fixture models command construction/path validation, not live kernel
    # isolation. Keep it portable without changing Python's global platform.
    tmp_path = tmp_path.resolve()
    monkeypatch.setattr(sandbox, "sys", SimpleNamespace(platform="linux"))
    root = tmp_path / "image"
    root.mkdir()
    for name in ("input", "output", "code", "models", "tmp", "proc", "dev"):
        (root / name).mkdir()
    (root / "markitdown-runtime.json").write_text(
        json.dumps(
            {
                "profile": sandbox.PROFILE,
                "image_id": "sha256:" + "a" * 64,
            }
        )
    )
    python = root / "usr/bin/python3"
    python.parent.mkdir(parents=True)
    python.write_text("synthetic nonexecutable runtime fixture")
    python.chmod(0o700)
    data = tmp_path / "private"
    data.mkdir()
    launcher = tmp_path / "synthetic-bwrap"
    launcher.write_text("synthetic command-shape fixture; never launched")
    launcher.chmod(0o700)
    return SimpleNamespace(
        deployment_mode="production",
        sandbox_runtime_root=root,
        sandbox_bwrap=launcher,
        sandbox_python="/usr/bin/python3",
        data_dir=data,
    )


def test_local_is_explicitly_nonproduction():
    assert sandbox.execution_snapshot(None, "markitdown") == {
        "sandbox_profile": "local-development-v1",
        "isolation": "development-only",
        "production_boundary": False,
        "runtime_image_id": None,
    }


def test_production_requires_image_and_never_runs_host(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_text("synthetic")
    monkeypatch.setattr(
        conversion.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("host parser started"),
    )
    with pytest.raises(conversion.ConversionError, match="生产隔离"):
        conversion.run_conversion(
            source, ".txt", settings=SimpleNamespace(deployment_mode="production")
        )
    assert not list(tmp_path.glob("parser-*"))


def test_production_config_is_unavailable_without_image(tmp_path):
    settings = SimpleNamespace(
        deployment_mode="production",
        data_dir=tmp_path,
        docling_enabled=False,
        max_file_bytes=20 * 1024**2,
    )
    config = engines.engine_config(settings)
    assert config[0]["available"] is False
    assert "生产隔离" in config[0]["reason"]
    assert config[0]["max_pages"] is None
    assert config[1]["max_pages"] == 2


def test_production_command_mounts_only_approved_paths(image_settings):
    source = image_settings.data_dir / "source.txt"
    source.write_text("synthetic")
    output = image_settings.data_dir / "result.json"
    output.touch()
    argv, env = sandbox.command(
        image_settings,
        engine="markitdown",
        mode="convert",
        source=source,
        output=output,
        suffix=".txt",
    )
    assert "--unshare-all" in argv and "--disable-userns" in argv
    assert "--unshare-user" in argv and "--unshare-cgroup" in argv
    assert "--share-net" not in argv and "--unshare-user-try" not in argv
    assert "--not-a-security-boundary" not in argv
    assert str(image_settings.data_dir) not in argv
    assert str(Path.home()) not in argv
    rw = [argv[i + 1 : i + 3] for i, value in enumerate(argv) if value == "--bind"]
    assert rw == [[str(output), "/output/result.json"]]
    ro = [argv[i + 1 : i + 3] for i, value in enumerate(argv) if value == "--ro-bind"]
    assert [str(source), "/input/source.txt"] in ro
    assert [str(image_settings.sandbox_runtime_root), "/"] in ro
    assert len(ro) == 2 + len(sandbox.CODE_FILES)
    assert "OPENAI_API_KEY" not in env and "HTTPS_PROXY" not in env
    assert argv[argv.index("--size") + 1] == str(sandbox.TMP_BYTES)


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_production_refuses_nonlinux_before_launch(
    image_settings, monkeypatch, platform
):
    monkeypatch.setattr(sandbox, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(
        conversion.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("unsupported production platform launched parser"),
    )
    source = image_settings.data_dir / "source.txt"
    source.write_text("synthetic")
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.runtime_paths(image_settings, "markitdown")
    with pytest.raises(conversion.ConversionError, match="生产隔离"):
        conversion.run_conversion(source, ".txt", settings=image_settings)


@pytest.mark.parametrize(
    "target", ["input", "output", "models", "code", "tmp", "proc", "dev"]
)
def test_image_mount_targets_cannot_hide_host_state(image_settings, target):
    (image_settings.sandbox_runtime_root / target / "unexpected-secret").write_text(
        "synthetic"
    )
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.runtime_paths(image_settings, "markitdown")


def test_image_cannot_contain_application_data(image_settings):
    image_settings.data_dir = image_settings.sandbox_runtime_root / "private"
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.runtime_paths(image_settings, "markitdown")


def test_runtime_interpreter_cannot_escape_image(image_settings):
    python = image_settings.sandbox_runtime_root / "usr/bin/python3"
    python.unlink()
    python.symlink_to(sys.executable)
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.runtime_paths(image_settings, "markitdown")


@pytest.mark.parametrize("attack", ["symlink", "hardlink", "fifo", "directory"])
def test_result_rejects_special_files(tmp_path, attack):
    output, target = tmp_path / "output", tmp_path / "target"
    target.write_text('{"markdown":"secret"}')
    if attack == "symlink":
        output.symlink_to(target)
    elif attack == "hardlink":
        os.link(target, output)
    elif attack == "fifo":
        os.mkfifo(output)
    else:
        output.mkdir()
    with pytest.raises((OSError, conversion.ConversionError)):
        conversion.read_result(output)


def test_worker_html_is_never_a_trust_input(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_text("synthetic")

    original = conversion._supervise

    def supervise(command, env, directory, cancelled, **kwargs):
        if command[2] == "markitdown_web.preview":
            return original(command, env, directory, cancelled, **kwargs)
        (directory / "result.json").write_text(
            json.dumps(
                {
                    "markdown": "# Safe\n![remote](https://remote.invalid/x)\n[click](javascript:bad())\n<script>bad()</script>",
                    "html": '<script>remote()</script><img src="https://exfil.invalid/secret">',
                }
            )
        )

    monkeypatch.setattr(conversion, "_supervise", supervise)
    _, html = conversion.run_conversion(source, ".txt")
    assert "<script>" not in html and "<img" not in html
    assert "href=" not in html and "src=" not in html
    assert "remote()" not in html


def test_worker_error_cannot_exfiltrate_paths_or_content(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_text("synthetic")

    def supervise(command, env, directory, cancelled, **kwargs):
        (directory / "result.json").write_text(
            json.dumps({"error": "/secret/password document content"})
        )

    monkeypatch.setattr(conversion, "_supervise", supervise)
    with pytest.raises(conversion.ConversionError) as error:
        conversion.run_conversion(source, ".txt")
    assert "/secret" not in str(error.value) and "password" not in str(error.value)


def test_standard_cancel_before_spawn(tmp_path, monkeypatch):
    monkeypatch.setattr(
        conversion.subprocess, "Popen", lambda *a, **k: pytest.fail("spawned")
    )
    with pytest.raises(conversion.ConversionError, match="已取消"):
        conversion.run_conversion(
            tmp_path / "missing.txt", ".txt", cancelled=lambda: True
        )


def test_standard_cancel_during_local_child_reaps(tmp_path, monkeypatch):
    observed = []
    original = subprocess.Popen

    def record(*args, **kwargs):
        process = original(*args, **kwargs)
        observed.append(process)
        return process

    monkeypatch.setattr(conversion.subprocess, "Popen", record)
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        return checks >= 3

    with pytest.raises(conversion.ConversionError, match="已取消"):
        conversion._supervise(
            [sys.executable, "-c", "import time;time.sleep(30)"],
            sandbox.environment(tmp_path),
            tmp_path,
            cancelled,
        )
    assert len(observed) == 1 and observed[0].poll() is not None
    if sys.platform == "linux":
        assert not sandbox._group_alive(observed[0].pid)


def test_termination_kills_group_even_after_leader_exit(monkeypatch):
    signals = []
    monkeypatch.setattr(sandbox.os, "killpg", lambda pid, sig: signals.append(sig))
    monkeypatch.setattr(sandbox, "_group_alive", lambda pid: False)
    sandbox.terminate(SimpleNamespace(pid=12345678, wait=lambda **kwargs: 0))
    assert signals == [signal.SIGTERM, signal.SIGKILL]


@pytest.mark.skipif(sys.platform != "linux", reason="Linux seccomp filter")
def test_kernel_filter_added_and_native_escapes_denied():
    script = f"""import runpy,ctypes,os,json,threading
from pathlib import Path
w=runpy.run_path({str(Path(sandbox.__file__))!r})
def count():
    return int(next(x.split()[1] for x in Path('/proc/self/status').read_text().splitlines() if x.startswith('Seccomp_filters:')))
before=count();w['enforce_worker_filter']();result={{'filter_added':count()>before}}
libc=ctypes.CDLL(None,use_errno=True)
for name,args in [('socket',(2,1,0)),('socketpair',(1,1,0,ctypes.c_void_p())),('setsid',()),('setpgid',(0,0)),('fork',())]:
    value=getattr(libc,name)(*args)
    if name=='fork' and value==0:os._exit(99)
    result[name]=[value,ctypes.get_errno()]
t=threading.Thread(target=lambda:result.update(thread=True));t.start();t.join()
print(json.dumps(result))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script], capture_output=True, timeout=8
    )
    assert result.returncode == 0, result.stderr.decode()
    values = json.loads(result.stdout)
    assert values.pop("filter_added") is True and values.pop("thread") is True
    assert all(value == [-1, 1] for value in values.values())


@pytest.mark.skipif(
    not os.environ.get("MARKITDOWN_TEST_RUNTIME_ROOT"),
    reason="Production gate requires separately provisioned approved Linux runtime image",
)
def test_actual_linux_boundary_hides_files_network_and_processes(tmp_path):
    """Run on the intended host; any namespace rejection is a hard test failure."""
    settings = SimpleNamespace(
        deployment_mode="production",
        data_dir=tmp_path,
        sandbox_runtime_root=Path(os.environ["MARKITDOWN_TEST_RUNTIME_ROOT"]),
        sandbox_bwrap=Path(
            os.environ.get("MARKITDOWN_SANDBOX_BWRAP", "/usr/bin/bwrap")
        ),
        sandbox_python=os.environ.get(
            "MARKITDOWN_TEST_SANDBOX_PYTHON", "/usr/bin/python3"
        ),
    )
    secret = tmp_path / "synthetic-other-job-secret"
    secret.write_text("MUST NOT BE VISIBLE")
    source, output = tmp_path / "source.txt", tmp_path / "result.json"
    source.write_text("allowed input")
    output.touch()
    argv, env = sandbox.command(
        settings,
        engine="markitdown",
        mode="convert",
        source=source,
        output=output,
        suffix=".txt",
    )
    names = ("user", "mnt", "pid", "net", "ipc", "uts", "cgroup")
    host_namespaces = {name: os.readlink(f"/proc/self/ns/{name}") for name in names}
    script = f"""import sys;sys.path.insert(0,'/code')
from markitdown_web.sandbox import enforce_worker_filter
from pathlib import Path
import os,socket,json
enforce_worker_filter()
r={{'source':Path('/input/source.txt').read_text(),'secret_visible':Path({str(secret)!r}).exists(),
'host_home':Path({str(Path.home())!r}).exists(),'other_input':Path('/input/sibling').exists()}}
r['namespaces']={{name:os.readlink('/proc/self/ns/'+name) for name in {names!r}}}
for name,op in [('network',lambda:socket.socket()),('fork',os.fork),('setsid',os.setsid),
('source_write',lambda:Path('/input/source.txt').write_text('changed')),
('root_write',lambda:Path('/host-file').write_text('changed'))]:
    try:op();r[name]=False
    except OSError:r[name]=True
Path('/output/result.json').write_text(json.dumps(r))
"""
    argv[argv.index("-c") + 1] = script
    process = subprocess.Popen(
        argv,
        env=env,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _, error = process.communicate(timeout=15)
        assert process.returncode == 0, error.decode()
    finally:
        sandbox.terminate(process)
    result = json.loads(output.read_text())
    child_namespaces = result.pop("namespaces")
    assert all(child_namespaces[name] != host_namespaces[name] for name in names)
    assert result.pop("source") == "allowed input"
    assert result.pop("secret_visible") is False
    assert result.pop("host_home") is False
    assert result.pop("other_input") is False
    assert all(result.values())
    assert source.read_text() == "allowed input"


@pytest.mark.parametrize("payload", ["&" * 300000, "[" * 300000])
def test_adversarial_preview_is_independent_timed_out_and_reaped(
    tmp_path, monkeypatch, payload
):
    from markitdown_web import preview

    monkeypatch.setattr(
        preview, "render_preview", lambda text: pytest.fail("rendered on supervisor")
    )
    observed = []
    original = subprocess.Popen

    def record(*args, **kwargs):
        process = original(*args, **kwargs)
        observed.append(process)
        return process

    monkeypatch.setattr(conversion.subprocess, "Popen", record)
    started = time.monotonic()
    with pytest.raises(conversion.ConversionError, match="超时"):
        conversion.validated_preview(
            payload, directory=tmp_path, deadline=started + 0.15
        )
    assert time.monotonic() - started < 3
    assert len(observed) == 1 and observed[0].poll() is not None
    assert not list(tmp_path.glob("preview-*"))
    assert not sandbox.active_workspaces()


def test_adversarial_preview_cancel_reaps_before_return(tmp_path, monkeypatch):
    observed = []
    original = subprocess.Popen

    def record(*args, **kwargs):
        process = original(*args, **kwargs)
        observed.append(process)
        return process

    monkeypatch.setattr(conversion.subprocess, "Popen", record)
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        return checks >= 4

    with pytest.raises(conversion.ConversionError, match="已取消"):
        conversion.validated_preview(
            "[" * 300000, directory=tmp_path, cancelled=cancelled
        )
    assert len(observed) == 1 and observed[0].poll() is not None
    assert not list(tmp_path.glob("preview-*"))


@pytest.mark.parametrize(
    "html",
    [
        "<script>bad</script>",
        '<a href="https://evil.invalid">x</a>',
        "<img src=x>",
        "<!DOCTYPE html>",
        "<p ",
        "<!--hidden-->",
    ],
)
def test_parent_html_grammar_rejects_all_active_markup(html):
    from markitdown_web.preview import validate_html

    assert not validate_html(html)


def test_parent_html_grammar_allows_inert_escaped_text():
    from markitdown_web.preview import validate_html

    assert validate_html(
        "<h1>Safe</h1><p>&lt;script&gt; &#60;img&gt;</p><br><a>text</a>"
    )


def test_workspace_registry_survives_until_exit(tmp_path):
    with sandbox.private_workspace(tmp_path, "engine-") as path:
        assert path in sandbox.active_workspaces()
        assert path.is_dir()
    assert path not in sandbox.active_workspaces() and not path.exists()


def test_real_standard_pdf_has_no_two_page_admission_cap(tmp_path):
    # A valid three-page PDF built from scratch, no external/private corpus.
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R] /Count 3 >>",
    ]
    for content in (6, 7, 8):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 9 0 R >> >> /Contents {content} 0 R >>".encode()
        )
    for number in (1, 2, 3):
        stream = f"BT /F1 18 Tf 50 750 Td (Synthetic page {number}) Tj ET".encode()
        objects.append(
            b"<< /Length "
            + str(len(stream)).encode()
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    source = tmp_path / "three-pages.pdf"
    source.write_bytes(data)
    metadata = {}
    markdown, html = conversion.run_conversion(
        source, ".pdf", runtime_metadata=metadata
    )
    assert all(f"Synthetic page {number}" in markdown for number in (1, 2, 3))
    assert "Synthetic page 3" in html
    assert (
        metadata["engine"] == "markitdown"
        and metadata["version_source"] == "worker_reported"
    )
    assert metadata["version"] and metadata["python"]
    assert "page_count" not in metadata
    assert not list(tmp_path.glob("parser-*")) and not list(tmp_path.glob("preview-*"))


@pytest.mark.parametrize("failure", [FileNotFoundError, PermissionError])
def test_local_rss_keeps_live_root_when_children_unavailable(monkeypatch, failure):
    def read(path, *args, **kwargs):
        if str(path).endswith("/status"):
            return "State:\tS (sleeping)\nVmRSS:\t1234 kB\n"
        raise failure("Synthetic unavailable children")

    monkeypatch.setattr(Path, "read_text", read)
    assert sandbox.process_tree_rss(101) == 1234 * 1024
    with pytest.raises(sandbox.ResourceMonitoringUnavailable):
        sandbox.process_tree_rss(101, require_tree=True)


def test_production_rss_sums_observed_wrapper_tree(monkeypatch):
    content = {
        "/proc/101/status": "State:\tS (sleeping)\nVmRSS:\t12 kB\n",
        "/proc/101/task/101/children": "102 103",
        "/proc/102/status": "State:\tR (running)\nVmRSS:\t100 kB\n",
        "/proc/102/task/102/children": "",
        "/proc/103/status": "State:\tS (sleeping)\nVmRSS:\t200 kB\n",
        "/proc/103/task/103/children": "",
    }
    monkeypatch.setattr(Path, "read_text", lambda path: content[str(path)])
    assert sandbox.process_tree_rss(101, require_tree=True) == 312 * 1024
    assert sandbox.process_tree_rss(101) == 12 * 1024


def test_unreadable_live_rss_is_not_zero(monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda path: "State:\tR (running)\n")
    with pytest.raises(sandbox.ResourceMonitoringUnavailable):
        sandbox.process_tree_rss(101)


def test_missing_root_requires_supervisor_exit_confirmation(monkeypatch):
    def read(path):
        raise FileNotFoundError("Synthetic missing root")

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(FileNotFoundError):
        sandbox.process_tree_rss(101)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process status")
def test_actual_live_child_reports_nonzero_rss():
    process = subprocess.Popen(
        [sys.executable, "-c", "import time;data=bytearray(1024*1024);time.sleep(10)"]
    )
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            rss = sandbox.process_tree_rss(process.pid)
            if rss > 1024 * 1024:
                break
            time.sleep(0.02)
        assert rss > 1024 * 1024
    finally:
        process.terminate()
        process.wait(timeout=3)


@pytest.mark.parametrize("current", [101, 102])
@pytest.mark.parametrize(
    "after", ["missing", "zombie", "dead", "live", "unknown", "denied"]
)
def test_production_rss_children_exit_race_requires_confirmed_death(
    monkeypatch, current, after
):
    reads = {}

    def read(path, *args, **kwargs):
        key = str(path)
        reads[key] = reads.get(key, 0) + 1
        if key == f"/proc/{current}/task/{current}/children":
            raise FileNotFoundError("Synthetic exit race")
        if key == f"/proc/{current}/status" and reads[key] > 1:
            if after == "missing":
                raise FileNotFoundError("Synthetic confirmed exit")
            if after == "denied":
                raise PermissionError("Synthetic unknown state")
            if after == "unknown":
                return "Name:\tfixture\n"
            state = {"zombie": "Z", "dead": "X", "live": "S"}[after]
            return f"State:\t{state}\nVmRSS:\t10 kB\n"
        if key.endswith("/status"):
            return "State:\tS\nVmRSS:\t10 kB\n"
        if key == "/proc/101/task/101/children":
            return "102"
        return ""

    monkeypatch.setattr(Path, "read_text", read)
    if after in {"live", "unknown", "denied"}:
        with pytest.raises(sandbox.ResourceMonitoringUnavailable):
            sandbox.process_tree_rss(101, require_tree=True)
    elif current == 101 and after == "missing":
        with pytest.raises(FileNotFoundError):
            sandbox.process_tree_rss(101, require_tree=True)
    else:
        # Previously measured RSS is retained; no unavailable live memory is zeroed.
        assert (
            sandbox.process_tree_rss(101, require_tree=True)
            == (10 if current == 101 else 20) * 1024
        )
    assert reads[f"/proc/{current}/status"] == 2


def test_production_rss_children_permission_denial_is_not_an_exit_race(monkeypatch):
    status_reads = []

    def read(path, *args, **kwargs):
        if str(path).endswith("/status"):
            status_reads.append(str(path))
            return "State:\tS\nVmRSS:\t10 kB\n"
        raise PermissionError("Synthetic live tree denied")

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(sandbox.ResourceMonitoringUnavailable):
        sandbox.process_tree_rss(101, require_tree=True)
    assert status_reads == ["/proc/101/status"]


def test_production_rss_malformed_children_remains_failure(monkeypatch):
    def read(path, *args, **kwargs):
        if str(path).endswith("/status"):
            return "State:\tS\nVmRSS:\t10 kB\n"
        return "not-a-pid"

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(sandbox.ResourceMonitoringUnavailable):
        sandbox.process_tree_rss(101, require_tree=True)
