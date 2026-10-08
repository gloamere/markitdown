"""Standalone isolated-entry worker: no service imports or inherited credentials.

Production runs in the fixed Linux filesystem/PID/network namespace profile.
Every mode, including preflight/probe, installs the kernel syscall filter first.
"""
from __future__ import annotations

import ctypes
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
    expected_parent = os.environ.get("MARKITDOWN_PARENT_PID")
    if expected_parent and os.getppid() != int(expected_parent):
        raise RuntimeError("Parent changed")
    small = mode != "convert"
    resource.setrlimit(resource.RLIMIT_CPU, (5 if small else 90,) * 2)
    resource.setrlimit(
        resource.RLIMIT_AS, ((1024 if small else 8192) * 1024 * 1024,) * 2
    )
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    if os.environ.get("MARKITDOWN_SANDBOX_PROFILE") == "linux-bwrap-v1":
        resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))
    cpus = sorted(os.sched_getaffinity(0))
    os.sched_setaffinity(0, cpus[:2])
    load_module("sandbox").enforce_worker_filter()


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
    return {"markdown": markdown, "metadata": metadata}


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
