"""Optional engine admission and subprocess supervision, without heavy imports."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .conversion import MAX_HTML_BYTES, MAX_RESULT_BYTES, ConversionError
from .docling_adapter import (
    ASSETS,
    MAX_FILE_BYTES,
    MAX_MARKDOWN_BYTES,
    MAX_PAGES,
    OFFLINE_ENV,
    PROFILE,
    VERSION,
)

WALL_SECONDS = 60
PREFLIGHT_SECONDS = 8
RSS_BYTES = 3 * 1024**3
TEMP_BYTES = 512 * 1024**2
MIN_FREE_BYTES = 1024**3
WORKER = Path(__file__).with_name("docling_worker.py")
ERRORS = {
    "runtime_unavailable": "PDF 增强环境尚未就绪，请联系管理员或选择普通转换",
    "size_limit": "PDF 增强仅支持不超过 10 MiB 的文件",
    "page_limit": "PDF 增强仅支持 1 至 2 页，请先拆分 PDF",
    "invalid_pdf": "无法读取 PDF，文件可能已损坏或加密",
    "incomplete": "PDF 增强未完整完成，已拒绝部分结果；请拆分文档或另行选择普通转换",
    "output_limit": "转换结果超过 2 MiB，请拆分文档后重试",
    "no_text": "没有提取到文字；PDF 增强未开启 OCR，扫描件暂不支持",
    "conversion_failed": "PDF 增强失败，文件可能损坏或超出资源限制",
}
_probe_lock = threading.Lock()
_preflight_slot = threading.BoundedSemaphore(1)
_probe_cache: dict[tuple, tuple[float, bool]] = {}


def _error(code: str) -> ConversionError:
    return ConversionError(ERRORS.get(code, ERRORS["conversion_failed"]))


def _paths(settings: Any) -> tuple[Path, Path]:
    if not getattr(settings, "docling_enabled", False) or sys.platform != "linux":
        raise _error("runtime_unavailable")
    python = getattr(settings, "docling_python", None)
    models = getattr(settings, "docling_models", None)
    if python is None or models is None:
        raise _error("runtime_unavailable")
    python, models = Path(python), Path(models)
    if not python.is_absolute() or not models.is_absolute():
        raise _error("runtime_unavailable")
    if not python.is_file() or not os.access(python, os.X_OK):
        raise _error("runtime_unavailable")
    if any(path.is_symlink() for path in (models, *models.parents)):
        raise _error("runtime_unavailable")
    return python, models


def _model_signature(models: Path) -> tuple:
    signature = []
    for relative, (size, _) in ASSETS.items():
        path = models / relative
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise _error("runtime_unavailable")
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size != size:
            raise _error("runtime_unavailable")
        signature.append(
            (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        )
    return tuple(signature)


def _verify_models(models: Path) -> None:
    for relative, (_, digest) in ASSETS.items():
        with (models / relative).open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != digest:
                raise _error("runtime_unavailable")


def _private_temp(settings: Any):
    root = Path(settings.data_dir)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return tempfile.TemporaryDirectory(prefix="engine-", dir=root)


def _environment(directory: Path) -> dict[str, str]:
    return {
        "PATH": os.defpath,
        "HOME": str(directory),
        "TMPDIR": str(directory),
        "TMP": str(directory),
        "TEMP": str(directory),
        "PYTHONUTF8": "1",
        "MARKITDOWN_PARENT_PID": str(os.getpid()),
        "OPENBLAS_NUM_THREADS": "2",
        "OMP_NUM_THREADS": "2",
        "MKL_NUM_THREADS": "2",
        "NUMEXPR_NUM_THREADS": "2",
        "TOKENIZERS_PARALLELISM": "false",
        "HF_HOME": str(directory / "hf"),
        "TORCH_HOME": str(directory / "torch"),
        "XDG_CACHE_HOME": str(directory / "cache"),
        **OFFLINE_ENV,
    }


def _rss(pid: int) -> int:
    # Linux-only optional engine fails closed if monitoring becomes unavailable.
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    return 0  # Process may already be a zombie awaiting poll().


def _scratch_bytes(directory: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
        for name in files:
            try:
                info = (Path(root) / name).lstat()
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
            except FileNotFoundError:
                continue
    return total


def _terminate(process: subprocess.Popen) -> None:
    # Signal the group even if its leader has already exited. The child filter
    # also rejects process creation, so converter threads are the only members.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        # Keep the physical queue slot until the OS confirms process termination.
        # An uninterruptible kernel wait must not admit a replacement parser.
        process.wait()


def _read_result(output: Path) -> dict:
    descriptor = os.open(output, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RESULT_BYTES:
            raise _error("output_limit")
        raw = handle.read(MAX_RESULT_BYTES + 1)
    if len(raw) > MAX_RESULT_BYTES:
        raise _error("output_limit")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise _error("conversion_failed")
    if result.get("error"):
        raise _error(str(result["error"]))
    return result


def _invoke(
    settings: Any,
    mode: str,
    source: Path,
    directory: Path,
    cancelled: Callable[[], bool],
) -> dict:
    python, models = _paths(settings)
    output = directory / "engine-result.json"
    output.touch(mode=0o600, exist_ok=False)
    timeout = WALL_SECONDS if mode == "convert" else PREFLIGHT_SECONDS
    rss_limit = RSS_BYTES if mode == "convert" else 512 * 1024**2
    started = time.monotonic()
    process: subprocess.Popen | None = None
    try:
        if cancelled():
            raise ConversionError("任务已取消")
        if shutil.disk_usage(directory).free < MIN_FREE_BYTES:
            raise ConversionError("临时存储空间不足，请稍后重试")
        process = subprocess.Popen(
            [
                str(python),
                "-I",
                "-B",
                str(WORKER),
                mode,
                str(source),
                str(output),
                str(models),
            ],
            cwd=directory,
            env=_environment(directory),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        while process.poll() is None:
            if cancelled():
                raise ConversionError("任务已取消")
            if time.monotonic() - started > timeout:
                raise ConversionError(f"PDF 增强超时（{timeout} 秒），请拆分文档后重试")
            try:
                rss = _rss(process.pid)
            except FileNotFoundError:
                if process.poll() is not None:
                    break
                raise _error("conversion_failed")
            if rss > rss_limit or _scratch_bytes(directory) > TEMP_BYTES:
                raise ConversionError("PDF 增强超出资源限制，请拆分文档后重试")
            if shutil.disk_usage(directory).free < MIN_FREE_BYTES:
                raise ConversionError("临时存储空间不足，请稍后重试")
            time.sleep(0.05)
        if cancelled():
            raise ConversionError("任务已取消")
        if process.returncode != 0:
            raise _error("conversion_failed")
        return _read_result(output)
    except ConversionError:
        raise
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise _error("conversion_failed") from exc
    finally:
        if process is not None:
            _terminate(process)


def ensure_engine_available(engine: str, settings: Any) -> None:
    if engine == "markitdown":
        return
    if engine != "docling":
        raise ConversionError("未知转换引擎")
    try:
        python, models = _paths(settings)
        stamp = python.stat()
        key = (str(python), stamp.st_mtime_ns, str(models), _model_signature(models))
        with _probe_lock:
            cached = _probe_cache.get(key)
            if cached and time.monotonic() - cached[0] < 30:
                if not cached[1]:
                    raise _error("runtime_unavailable")
                return
            try:
                _verify_models(models)
                with _private_temp(settings) as temporary:
                    result = _invoke(
                        settings,
                        "probe",
                        Path(temporary),
                        Path(temporary),
                        lambda: False,
                    )
                ready = result.get("ready") is True and result.get("version") == VERSION
            except ConversionError:
                ready = False
            _probe_cache.clear()
            _probe_cache[key] = (time.monotonic(), ready)
            if not ready:
                raise _error("runtime_unavailable")
    except (OSError, ValueError) as exc:
        raise _error("runtime_unavailable") from exc


def validate_engine_uploads(
    engine: str, files: list[dict[str, Any]], settings: Any
) -> None:
    ensure_engine_available(engine, settings)
    if engine != "docling":
        return
    if not _preflight_slot.acquire(blocking=False):
        raise ConversionError("PDF 页数校验繁忙，请稍后重试")
    try:
        for upload in files:
            data = upload.get("data")
            if (
                upload.get("suffix") != ".pdf"
                or not isinstance(data, bytes)
                or not data.startswith(b"%PDF-")
            ):
                raise ConversionError("PDF 增强仅支持有效的 PDF 文件")
            if len(data) > MAX_FILE_BYTES:
                raise _error("size_limit")
            with _private_temp(settings) as temporary:
                directory = Path(temporary)
                source = directory / "source.pdf"
                source.touch(mode=0o600)
                source.write_bytes(data)
                result = _invoke(
                    settings, "preflight", source, directory, lambda: False
                )
                if (
                    type(result.get("page_count")) is not int
                    or not 1 <= result["page_count"] <= MAX_PAGES
                ):
                    raise _error("page_limit")
    finally:
        _preflight_slot.release()


def run_docling_conversion(
    path: Path, settings: Any, cancelled: Callable[[], bool]
) -> tuple[str, str, dict]:
    ensure_engine_available("docling", settings)
    if (
        any(parent.is_symlink() for parent in (path, *path.parents))
        or not path.is_file()
    ):
        raise _error("invalid_pdf")
    # Scratch outputs have their own directory, removed on every exit path.
    with tempfile.TemporaryDirectory(prefix="docling-", dir=path.parent) as temporary:
        result = _invoke(settings, "convert", path, Path(temporary), cancelled)
    markdown, metadata = result.get("markdown"), result.get("metadata")
    if (
        not isinstance(markdown, str)
        or not markdown.strip()
        or len(markdown.encode("utf-8")) > MAX_MARKDOWN_BYTES
    ):
        raise _error("output_limit")
    if not isinstance(metadata, dict):
        raise _error("conversion_failed")
    pages, duration = metadata.get("page_count"), metadata.get("duration_seconds")
    if (
        type(pages) is not int
        or not 1 <= pages <= MAX_PAGES
        or not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not math.isfinite(duration)
        or not 0 <= duration <= WALL_SECONDS
    ):
        raise _error("conversion_failed")
    if (
        metadata.get("engine") != "docling"
        or metadata.get("version") != VERSION
        or metadata.get("profile") != PROFILE
        or metadata.get("ocr") is not False
    ):
        raise _error("conversion_failed")
    html = result.get("html")
    if not isinstance(html, str) or len(html.encode("utf-8")) > MAX_HTML_BYTES:
        raise _error("output_limit")
    return (
        markdown,
        html,
        {
            "engine": "docling",
            "version": VERSION,
            "profile": PROFILE,
            "ocr": False,
            "page_count": pages,
            "duration_seconds": duration,
            "warnings": [],
        },
    )


def engine_config(settings: Any) -> list[dict[str, Any]]:
    reason = ""
    try:
        ensure_engine_available("docling", settings)
    except ConversionError as exc:
        reason = str(exc)
    return [
        {
            "id": "markitdown",
            "label": "普通转换",
            "available": True,
            "reason": "",
            "max_pages": None,
            "max_file_bytes": settings.max_file_bytes,
            "timeout_seconds": 45,
            "ocr": False,
            "description": "速度优先，支持 PDF、Word、Excel 和文本",
        },
        {
            "id": "docling",
            "label": "PDF 增强",
            "available": not reason,
            "reason": reason,
            "max_pages": MAX_PAGES,
            "max_file_bytes": MAX_FILE_BYTES,
            "timeout_seconds": WALL_SECONDS,
            "pipeline_timeout_seconds": 30,
            "max_output_bytes": MAX_MARKDOWN_BYTES,
            "concurrency": 1,
            "ocr": False,
            "description": "改善短 PDF 的多栏阅读顺序和表格；最多 2 页，不支持扫描件，处理较慢",
        },
    ]
