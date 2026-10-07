"""Fixed Linux parser boundary and shared supervision helpers.

Production requires an operator-provisioned *minimal* runtime root filesystem.
It is never inferred from /, /usr, a home, a virtualenv, or the application data
root. The image must contain only the pinned runtime and dependencies, no
credentials or documents. Package/model provisioning is a separate offline
administrative step. This module never provisions or weakens host security.

The root is read-only. Only one input file, approved worker code/model files,
one already-open output inode and a size-bounded private tmpfs are added. PID,
network, user, IPC, UTS and cgroup namespaces are mandatory. Parser entrypoints
then install an irreversible syscall filter before importing any parser.
Local mode is intentionally NOT a production filesystem security boundary.
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

PROFILE = "linux-bwrap-v1"
CODE = Path(__file__).parent
CODE_FILES = (
    "__init__.py",
    "worker.py",
    "conversion.py",
    "sandbox.py",
    "preview.py",
    "docling_worker.py",
    "docling_adapter.py",
)
TMP_BYTES = 512 * 1024**2
UNAVAILABLE = "Linux 生产隔离环境尚未就绪，未启动解析；请联系管理员"


class SandboxUnavailable(RuntimeError):
    """The mandatory deployment boundary could not be constructed."""


def production(settings: Any) -> bool:
    mode = getattr(settings, "deployment_mode", "local")
    if mode not in {"local", "production"}:
        raise SandboxUnavailable(UNAVAILABLE)
    return mode == "production"


def execution_snapshot(settings: Any, engine: str) -> dict[str, Any]:
    secured = production(settings)
    image_id = None
    if secured:
        try:
            root, _, _ = runtime_paths(settings, engine)
            image_id = json.loads((root / "markitdown-runtime.json").read_bytes())[
                "image_id"
            ]
        except (OSError, ValueError, KeyError, SandboxUnavailable):
            pass
    return {
        "runtime_image_id": image_id,
        "sandbox_profile": PROFILE if secured else "local-development-v1",
        "isolation": "linux-namespaces-seccomp" if secured else "development-only",
        "production_boundary": secured,
    }


def _regular(path: Path) -> None:
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise SandboxUnavailable(UNAVAILABLE)
    if not stat.S_ISREG(path.stat().st_mode):
        raise SandboxUnavailable(UNAVAILABLE)


def _directory(path: Path) -> None:
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise SandboxUnavailable(UNAVAILABLE)
    if not stat.S_ISDIR(path.stat().st_mode):
        raise SandboxUnavailable(UNAVAILABLE)


def runtime_paths(settings: Any, engine: str) -> tuple[Path, Path, str]:
    if sys.platform != "linux" or getattr(settings, "sandbox_mode", "linux") != "linux":
        raise SandboxUnavailable(UNAVAILABLE)
    root_value = getattr(settings, "sandbox_runtime_root", None)
    if root_value is None:
        raise SandboxUnavailable(UNAVAILABLE)
    root = Path(root_value)
    _directory(root)
    # A filesystem tree, not a host directory selected for convenience. Requiring
    # the manifest gives deployment review a pinned artifact identity; it does
    # not certify the contents, which still require an image supply-chain review.
    if root == Path("/") or root in (Path("/usr"), Path("/opt"), Path("/home")):
        raise SandboxUnavailable(UNAVAILABLE)
    manifest_path = root / "markitdown-runtime.json"
    _regular(manifest_path)
    if manifest_path.stat().st_size > 4096:
        raise SandboxUnavailable(UNAVAILABLE)
    manifest = json.loads(manifest_path.read_bytes())
    if not isinstance(manifest, dict) or manifest.get("profile") != PROFILE:
        raise SandboxUnavailable(UNAVAILABLE)
    image_id = manifest.get("image_id")
    if (
        not isinstance(image_id, str)
        or not image_id.startswith("sha256:")
        or len(image_id) != 71
    ):
        raise SandboxUnavailable(UNAVAILABLE)
    try:
        int(image_id[7:], 16)
    except ValueError as exc:
        raise SandboxUnavailable(UNAVAILABLE) from exc
    data_dir = Path(getattr(settings, "data_dir", "/nonexistent-data")).resolve()
    if root == data_dir or root in data_dir.parents or data_dir in root.parents:
        raise SandboxUnavailable(UNAVAILABLE)
    # No production image may contain preexisting writable mount targets or
    # application state. Empty targets are created by bwrap, never followed.
    for target in ("input", "output", "code", "models", "tmp", "proc", "dev"):
        node = root / target
        if node.is_symlink() or not node.is_dir() or any(node.iterdir()):
            raise SandboxUnavailable(UNAVAILABLE)
    bwrap = Path(getattr(settings, "sandbox_bwrap", "/usr/bin/bwrap"))
    _regular(bwrap)
    if not os.access(bwrap, os.X_OK):
        raise SandboxUnavailable(UNAVAILABLE)
    field = "sandbox_docling_python" if engine == "docling" else "sandbox_python"
    python = str(
        getattr(
            settings,
            field,
            "/opt/docling/bin/python" if engine == "docling" else "/usr/bin/python3",
        )
    )
    inside = Path(python)
    if not inside.is_absolute() or ".." in inside.parts:
        raise SandboxUnavailable(UNAVAILABLE)
    # Image symlinks (e.g. python -> python3.12) are permitted only when their
    # resolved host location remains within the dedicated image.
    interpreter = (root / python.lstrip("/")).resolve()
    if (
        not interpreter.is_relative_to(root)
        or not interpreter.is_file()
        or not os.access(interpreter, os.X_OK)
    ):
        raise SandboxUnavailable(UNAVAILABLE)
    return root, bwrap, python


def environment(directory: Path, *, threads: int = 1) -> dict[str, str]:
    return {
        "PATH": os.defpath,
        "HOME": str(directory),
        "TMPDIR": str(directory),
        "TEMP": str(directory),
        "TMP": str(directory),
        "PYTHONUTF8": "1",
        "ORT_DISABLE_TELEMETRY": "1",
        "MARKITDOWN_PARENT_PID": str(os.getpid()),
        "OPENBLAS_NUM_THREADS": str(threads),
        "OMP_NUM_THREADS": str(threads),
        "MKL_NUM_THREADS": str(threads),
        "NUMEXPR_NUM_THREADS": str(threads),
        "TOKENIZERS_PARALLELISM": "false",
    }


def command(
    settings: Any,
    *,
    engine: str,
    mode: str,
    source: Path | None,
    output: Path,
    suffix: str = ".pdf",
    models: Path | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Construct only fixed commands; callers cannot submit commands/mounts/env."""
    if engine not in {"markitdown", "docling", "preview"} or mode not in {
        "convert",
        "probe",
        "preflight",
        "preview",
    }:
        raise SandboxUnavailable(UNAVAILABLE)
    if (
        mode
        not in {
            "markitdown": {"convert", "probe"},
            "docling": {"convert", "probe", "preflight"},
            "preview": {"preview"},
        }[engine]
    ):
        raise SandboxUnavailable(UNAVAILABLE)
    if suffix not in {".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv", ".json"}:
        raise SandboxUnavailable(UNAVAILABLE)
    root, bwrap, python = runtime_paths(settings, engine)
    _regular(output)
    if source is not None:
        _regular(source)
    argv = [
        str(bwrap),
        "--unshare-all",
        "--die-with-parent",
        "--disable-userns",
        "--cap-drop",
        "ALL",
        "--clearenv",
        "--ro-bind",
        str(root),
        "/",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--size",
        str(TMP_BYTES),
        "--tmpfs",
        "/tmp",
        "--size",
        "1048576",
        "--tmpfs",
        "/input",
        "--size",
        "1048576",
        "--tmpfs",
        "/output",
        "--size",
        "1048576",
        "--tmpfs",
        "/code",
        "--dir",
        "/code/markitdown_web",
        "--size",
        "1048576",
        "--tmpfs",
        "/models",
    ]
    for name in CODE_FILES:
        approved = CODE / name
        _regular(approved)
        argv.extend(["--ro-bind", str(approved), "/code/markitdown_web/" + name])
    if source is not None:
        argv.extend(["--ro-bind", str(source), "/input/source" + suffix])
    # No directory from the service's jobs tree is ever mounted. Even output is
    # only an inode, so a hostile worker cannot replace it with a link or FIFO.
    argv.extend(["--bind", str(output), "/output/result.json"])
    if engine == "docling":
        if models is None:
            raise SandboxUnavailable(UNAVAILABLE)
        from .docling_adapter import ASSETS

        _directory(models)
        for relative in ASSETS:
            asset = models / relative
            _regular(asset)
            argv.extend(["--ro-bind", str(asset), "/models/" + relative])
    for target in ("/input", "/output", "/code", "/models", "/proc", "/dev"):
        argv.extend(["--remount-ro", target])
    env = environment(Path("/tmp"), threads=2 if engine == "docling" else 1)
    env.pop("MARKITDOWN_PARENT_PID")  # PID-namespace parent differs from host.
    env["MARKITDOWN_SANDBOX_PROFILE"] = PROFILE
    env["MARKITDOWN_WORKER_MODE"] = mode
    if engine == "docling":
        from .docling_adapter import OFFLINE_ENV

        env.update(OFFLINE_ENV)
        env.update(
            HF_HOME="/tmp/hf", TORCH_HOME="/tmp/torch", XDG_CACHE_HOME="/tmp/cache"
        )
    for key, value in env.items():
        argv.extend(["--setenv", key, value])
    argv.extend(["--chdir", "/tmp", "--"])
    if engine in {"markitdown", "preview"}:
        entry = (
            "import sys;sys.path.insert(0,'/code');from markitdown_web.preview import worker_main;worker_main()"
            if engine == "preview"
            else "import sys;sys.path.insert(0,'/code');from markitdown_web.worker import main;main()"
        )
        argv.extend(
            [
                python,
                "-I",
                "-B",
                "-c",
                entry,
                "/input/source" + suffix,
                suffix,
                "/output/result.json",
            ]
        )
    else:
        argv.extend(
            [
                python,
                "-I",
                "-B",
                "/code/markitdown_web/docling_worker.py",
                mode,
                "/input/source.pdf" if source is not None else "/input/unused",
                "/output/result.json",
                "/models",
            ]
        )
    return argv, environment(Path("/tmp"))


def enforce_worker_filter() -> None:
    """Fail-closed seccomp: native sockets/process escapes are denied, threads allowed."""
    if sys.platform != "linux":
        raise RuntimeError("Linux syscall enforcement required")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise RuntimeError("No-new-privileges enforcement unavailable")
    sec = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    sec.seccomp_init.argtypes = [ctypes.c_uint32]
    sec.seccomp_init.restype = ctypes.c_void_p
    sec.seccomp_rule_add.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    sec.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    sec.seccomp_syscall_resolve_name.restype = ctypes.c_int
    sec.seccomp_load.argtypes = [ctypes.c_void_p]
    sec.seccomp_release.argtypes = [ctypes.c_void_p]
    context = sec.seccomp_init(0x7FFF0000)
    if not context:
        raise RuntimeError("Syscall enforcement unavailable")
    try:
        for name in (
            "socket",
            "socketpair",
            "connect",
            "bind",
            "listen",
            "accept",
            "accept4",
            "sendto",
            "sendmsg",
            "sendmmsg",
            "recvfrom",
            "recvmsg",
            "recvmmsg",
            "io_uring_setup",
            "io_uring_enter",
            "io_uring_register",
            "fork",
            "vfork",
            "setsid",
            "setpgid",
            "unshare",
            "setns",
            "execve",
            "execveat",
            "mount",
            "umount2",
            "pivot_root",
            "chroot",
            "ptrace",
            "process_vm_readv",
            "process_vm_writev",
            "open_by_handle_at",
            "bpf",
            "keyctl",
            "add_key",
            "request_key",
            "userfaultfd",
        ):
            number = sec.seccomp_syscall_resolve_name(name.encode("ascii"))
            if (
                number >= 0
                and sec.seccomp_rule_add(context, 0x00050000 | errno.EPERM, number, 0)
                != 0
            ):
                raise RuntimeError("Syscall rule failed")

        class ArgCompare(ctypes.Structure):
            _fields_ = [
                ("arg", ctypes.c_uint),
                ("op", ctypes.c_int),
                ("datum_a", ctypes.c_uint64),
                ("datum_b", ctypes.c_uint64),
            ]

        sec.seccomp_rule_add_array.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.POINTER(ArgCompare),
        ]
        clone = sec.seccomp_syscall_resolve_name(b"clone")
        if (
            clone >= 0
            and sec.seccomp_rule_add_array(
                context,
                0x00050000 | errno.EPERM,
                clone,
                1,
                ctypes.byref(ArgCompare(0, 7, 0x10000, 0)),
            )
            != 0
        ):
            raise RuntimeError("Process clone restriction failed")
        clone3 = sec.seccomp_syscall_resolve_name(b"clone3")
        if (
            clone3 >= 0
            and sec.seccomp_rule_add(context, 0x00050000 | errno.ENOSYS, clone3, 0) != 0
        ):
            raise RuntimeError("Process clone restriction failed")
        if sec.seccomp_load(context) != 0:
            raise RuntimeError("Syscall enforcement unavailable")
    finally:
        sec.seccomp_release(context)
    libc.socket.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
    descriptor = libc.socket(2, 1, 0)
    if descriptor >= 0:
        os.close(descriptor)
        raise RuntimeError("Network filter not active")
    if ctypes.get_errno() != errno.EPERM:
        raise RuntimeError("Network filter not verified")


def process_tree_rss(pid: int) -> int:
    """Sample the complete trusted-wrapper tree, not merely the bwrap parent."""
    total = 0
    pending = [pid]
    visited = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        try:
            status = Path(f"/proc/{current}/status").read_text()
            children = Path(f"/proc/{current}/task/{current}/children").read_text()
        except FileNotFoundError:
            continue
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                total += int(line.split()[1]) * 1024
                break
        pending.extend(map(int, children.split()))
    return total


def _group_alive(group: int) -> bool:
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    # A dead orphan awaiting its init reaper holds no resources and cannot write.
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "stat").read_text()
            fields = raw[raw.rfind(")") + 2 :].split()
            if int(fields[2]) == group and fields[0] not in {"Z", "X"}:
                return True
        except (FileNotFoundError, ProcessLookupError):
            continue
    return False


def terminate(process: subprocess.Popen) -> None:
    """Reap before returning, even if leader exited while a descendant survived."""
    if os.name != "posix":
        if process.poll() is None:
            process.kill()
        process.wait()
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        if sig == signal.SIGTERM:
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
    process.wait()
    # Process creation/group changes are denied before parser imports in Linux.
    # The PID namespace also kills all descendants when its init exits. Holding
    # this wait intentionally retains the physical scheduler slot for D-state.
    if sys.platform == "linux":
        while _group_alive(process.pid):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                break
            time.sleep(0.02)


_workspace_lock = threading.Lock()
_workspaces: set[Path] = set()


def active_workspaces() -> set[Path]:
    with _workspace_lock:
        return set(_workspaces)


@contextmanager
def private_workspace(root: Path, prefix: str) -> Iterator[Path]:
    """Register scratch through complete parser reap and directory cleanup."""
    temporary = tempfile.TemporaryDirectory(prefix=prefix, dir=root)
    path = Path(temporary.name).resolve()
    with _workspace_lock:
        _workspaces.add(path)
    try:
        yield path
    finally:
        try:
            temporary.cleanup()
        finally:
            with _workspace_lock:
                _workspaces.discard(path)
