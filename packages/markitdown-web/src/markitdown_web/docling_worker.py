"""Standalone isolated-entry worker: no service imports or inherited credentials.

Linux network syscalls are denied with a per-process seccomp filter. This is not
filesystem isolation; deploy untrusted parsing inside a filesystem sandbox too.
"""
from __future__ import annotations

import ctypes
import errno
import importlib.util
import json
import os
import signal
import stat
import sys
import time
from pathlib import Path


def restrict(mode: str) -> None:
    if sys.platform != "linux":
        raise RuntimeError("Linux resource and network enforcement required")
    import resource

    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise RuntimeError("Parent death enforcement failed")
    if os.getppid() != int(os.environ["MARKITDOWN_PARENT_PID"]):
        raise RuntimeError("Parent changed")
    small = mode != "convert"
    resource.setrlimit(resource.RLIMIT_CPU, (5 if small else 90,) * 2)
    resource.setrlimit(
        resource.RLIMIT_AS, ((1024 if small else 8192) * 1024 * 1024,) * 2
    )
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    cpus = sorted(os.sched_getaffinity(0))
    os.sched_setaffinity(0, cpus[:2])
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
    context = sec.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise RuntimeError("Network enforcement unavailable")
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
        ):
            number = sec.seccomp_syscall_resolve_name(name.encode("ascii"))
            if (
                number >= 0
                and sec.seccomp_rule_add(context, 0x00050000 | errno.EPERM, number, 0)
                != 0
            ):
                raise RuntimeError("Network rule failed")

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
        # Threads are necessary for Torch. Block process clones: CLONE_THREAD
        # must be set; clone3 cannot inspect pointed-to flags, so force libc's
        # supported clone fallback using ENOSYS.
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
            raise RuntimeError("Network enforcement unavailable")
    finally:
        sec.seccomp_release(context)
    # Verify that the actual kernel filter is active, not just a Python patch.
    libc.socket.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
    descriptor = libc.socket(2, 1, 0)
    if descriptor >= 0:
        os.close(descriptor)
        raise RuntimeError("Network filter not active")
    if ctypes.get_errno() != errno.EPERM:
        raise RuntimeError("Network filter not verified")


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(
        "_" + name, Path(__file__).with_name(name + ".py")
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Adapter unavailable")
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    return adapter


def page_count(path: Path, maximum: int) -> int:
    import pypdfium2 as pdfium

    with pdfium.PdfDocument(path) as document:
        pages = len(document)
        if not 1 <= pages <= maximum:
            raise ValueError("page_limit")
        return pages


def run(mode: str, source: Path, models: Path) -> dict:
    adapter = load_module("docling_adapter")
    adapter.runtime_version()
    if mode == "probe":
        # Metadata/spec inspection doesn't import Torch or load models.
        for name in ("pypdfium2", "torch", "docling", "cv2", "bleach", "markdown_it"):
            if importlib.util.find_spec(name) is None:
                return {"error": "runtime_unavailable"}
        return {"ready": True, "version": adapter.VERSION}
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > adapter.MAX_FILE_BYTES:
        return {"error": "size_limit"}
    with source.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            return {"error": "invalid_pdf"}
    try:
        pages = page_count(source, adapter.MAX_PAGES)
    except ValueError:
        return {"error": "page_limit"}
    except Exception:
        return {"error": "invalid_pdf"}
    if mode == "preflight":
        return {"page_count": pages}
    started = time.monotonic()
    markdown, metadata = adapter.convert(source, models)
    metadata["duration_seconds"] = round(time.monotonic() - started, 3)
    preview = load_module("preview").render_preview(markdown)
    if len(preview.encode("utf-8")) > 4 * 1024**2:
        raise adapter.AdapterError("output_limit")
    return {"markdown": markdown, "html": preview, "metadata": metadata}


def main() -> None:
    mode, source, output, models = sys.argv[1:]
    try:
        restrict(mode)
        result = run(mode, Path(source), Path(models))
    except Exception as exc:
        # No parser text, traceback, path, or document content crosses this boundary.
        code = str(exc) if type(exc).__name__ == "AdapterError" else "conversion_failed"
        result = {
            "error": code
            if code in {"runtime_unavailable", "incomplete", "output_limit", "no_text"}
            else "conversion_failed"
        }
    Path(output).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
