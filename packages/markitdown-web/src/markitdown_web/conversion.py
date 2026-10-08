"""Disposable conversion workers with an explicit production isolation boundary."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import sandbox

MAX_MARKDOWN_BYTES = 2 * 1024 * 1024
CONVERSION_TIMEOUT = 45
MAX_HTML_BYTES = 4 * 1024 * 1024
MAX_RESULT_BYTES = 16 * 1024 * 1024
PROFILE = "markitdown-local-v1"
SAFE_ERRORS = {
    "invalid_office": "文件内容与扩展名不符，请上传有效的 Office 文档",
    "unsafe_office": "文档包含不安全或过大的压缩条目，已拒绝处理",
    "invalid_pdf": "文件内容与扩展名不符，请上传有效的 PDF",
    "invalid_text": "此文件不是可识别的文本文件",
    "output_limit": "转换结果超过 2 MiB，请拆分文档后重试",
    "no_text": "没有提取到文字；扫描版 PDF 需要 OCR，当前版本暂不支持",
    "conversion_failed": "无法转换此文件，可能已损坏、加密或格式不受支持",
}


class ConversionError(Exception):
    """A safe, user-facing conversion error."""


def read_result(output: Path) -> dict:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(output, flags)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size > MAX_RESULT_BYTES
        ):
            raise ConversionError(SAFE_ERRORS["output_limit"])
        raw = handle.read(MAX_RESULT_BYTES + 1)
    if len(raw) > MAX_RESULT_BYTES:
        raise ConversionError(SAFE_ERRORS["output_limit"])
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ConversionError(SAFE_ERRORS["conversion_failed"])
    return result


def validated_preview(
    markdown: Any,
    *,
    settings: Any = None,
    cancelled: Callable[[], bool] = lambda: False,
    directory: Path | None = None,
    deadline: float | None = None,
) -> tuple[str, str]:
    # Parser-controlled HTML is never accepted. Rebuild from bounded Markdown
    # with no HTML, images, href/src attributes, or network access in the view.
    if (
        not isinstance(markdown, str)
        or len(markdown.encode("utf-8")) > MAX_MARKDOWN_BYTES
    ):
        raise ConversionError(SAFE_ERRORS["output_limit"])
    if not markdown.strip():
        raise ConversionError(SAFE_ERRORS["no_text"])
    from .preview import validate_html

    if cancelled():
        raise ConversionError("任务已取消")
    root = (
        directory
        if directory is not None
        else Path(getattr(settings, "data_dir", tempfile.gettempdir()))
    )
    with sandbox.private_workspace(root, "preview-") as temporary:
        source = temporary / "source.md"
        output = temporary / "result.json"
        source.touch(mode=0o600, exist_ok=False)
        source.write_text(markdown, encoding="utf-8")
        output.touch(mode=0o600, exist_ok=False)
        if sandbox.production(settings):
            command, env = sandbox.command(
                settings,
                engine="preview",
                mode="preview",
                source=source,
                output=output,
                suffix=".md",
            )
        else:
            command = [
                sys.executable,
                "-m",
                "markitdown_web.preview",
                str(source),
                ".md",
                str(output),
            ]
            env = sandbox.environment(temporary)
        try:
            _supervise(command, env, temporary, cancelled, timeout=8, deadline=deadline)
            result = read_result(output)
        except (
            OSError,
            ValueError,
            subprocess.SubprocessError,
            sandbox.SandboxUnavailable,
        ) as exc:
            raise ConversionError("预览生成失败或超出资源限制") from exc
    html = result.get("html")
    if (
        result.get("error")
        or not isinstance(html, str)
        or len(html.encode("utf-8")) > MAX_HTML_BYTES
        or not validate_html(html)
    ):
        raise ConversionError("预览生成失败或超过 4 MiB，请拆分文档后重试")
    if cancelled():
        raise ConversionError("任务已取消")
    return markdown, html


def run_conversion(
    path: Path,
    suffix: str,
    *,
    cancelled: Callable[[], bool] = lambda: False,
    settings: Any = None,
    runtime_metadata: dict | None = None,
) -> tuple[str, str]:
    deadline = time.monotonic() + CONVERSION_TIMEOUT
    if suffix not in {".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv", ".json"}:
        raise ConversionError(SAFE_ERRORS["conversion_failed"])
    if cancelled():
        raise ConversionError("任务已取消")
    try:
        if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
            raise ConversionError(SAFE_ERRORS["conversion_failed"])
        # An attempt never trusts a previous attempt's output. The sole writable
        # host inode lives in a private directory and is removed on every exit.
        with sandbox.private_workspace(path.parent, "parser-") as temporary:
            directory = Path(temporary)
            output = directory / "result.json"
            output.touch(mode=0o600, exist_ok=False)
            if sandbox.production(settings):
                command, env = sandbox.command(
                    settings,
                    engine="markitdown",
                    mode="convert",
                    source=path,
                    output=output,
                    suffix=suffix,
                )
            else:
                command = [
                    sys.executable,
                    "-m",
                    "markitdown_web.worker",
                    str(path),
                    suffix,
                    str(output),
                ]
                env = sandbox.environment(directory)
                env["MARKITDOWN_LOCAL_WORKER"] = "1"
                if "SystemRoot" in os.environ:
                    env["SystemRoot"] = os.environ["SystemRoot"]
            _supervise(command, env, directory, cancelled, deadline=deadline)
            if cancelled():
                raise ConversionError("任务已取消")
            result = read_result(output)
        metadata = result.get("metadata")
        if runtime_metadata is not None and isinstance(metadata, dict):
            version, python = metadata.get("version"), metadata.get("python")
            if all(
                isinstance(v, str)
                and 0 < len(v) <= 64
                and v[0].isdigit()
                and v.isascii()
                and all(c.isalnum() or c in ".+-_" for c in v)
                for v in (version, python)
            ):
                runtime_metadata.update(
                    engine="markitdown",
                    profile=PROFILE,
                    version=version,
                    python=python,
                    version_source="worker_reported",
                )
        if result.get("error"):
            raise ConversionError(
                SAFE_ERRORS.get(str(result["error"]), SAFE_ERRORS["conversion_failed"])
            )
        answer = validated_preview(
            result.get("markdown"),
            settings=settings,
            cancelled=cancelled,
            directory=path.parent,
            deadline=deadline,
        )
        if cancelled():
            raise ConversionError("任务已取消")
        return answer
    except ConversionError:
        raise
    except sandbox.SandboxUnavailable as exc:
        raise ConversionError(sandbox.UNAVAILABLE) from exc
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        raise ConversionError(SAFE_ERRORS["conversion_failed"]) from exc


def _supervise(
    command: list[str],
    env: dict[str, str],
    directory: Path,
    cancelled: Callable[[], bool],
    *,
    timeout: float = CONVERSION_TIMEOUT,
    deadline: float | None = None,
) -> None:
    process: subprocess.Popen | None = None
    started = time.monotonic()
    stop_at = (
        min(started + timeout, deadline) if deadline is not None else started + timeout
    )
    try:
        if cancelled():
            raise ConversionError("任务已取消")
        if started >= stop_at:
            raise ConversionError("转换超时，请拆分文档后重试")
        process = subprocess.Popen(
            command,
            cwd=directory,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        while process.poll() is None:
            if cancelled():
                raise ConversionError("任务已取消")
            if time.monotonic() > stop_at:
                raise ConversionError(f"转换超时（{timeout:g} 秒），请拆分文档后重试")
            time.sleep(0.05)
        if cancelled():
            raise ConversionError("任务已取消")
        if process.returncode != 0:
            raise ConversionError(SAFE_ERRORS["conversion_failed"])
    finally:
        if process is not None:
            sandbox.terminate(process)


_availability_lock = threading.Lock()
_availability_cache: dict[tuple, tuple[float, bool]] = {}


def ensure_standard_available(settings: Any) -> None:
    """Probe the complete production launch, never fall back to a host parser."""
    try:
        if not sandbox.production(settings):
            return
        root, bwrap, python = sandbox.runtime_paths(settings, "markitdown")
        manifest = (root / "markitdown-runtime.json").stat()
        key = (
            str(root),
            str(bwrap),
            python,
            manifest.st_mtime_ns,
            (sandbox.CODE / "sandbox.py").stat().st_mtime_ns,
        )
        with _availability_lock:
            cached = _availability_cache.get(key)
            if cached and time.monotonic() - cached[0] < 30:
                if not cached[1]:
                    raise ConversionError(sandbox.UNAVAILABLE)
                return
            ready = False
            try:
                with sandbox.private_workspace(
                    Path(settings.data_dir), "engine-"
                ) as temporary:
                    directory = Path(temporary)
                    output = directory / "result.json"
                    output.touch(mode=0o600, exist_ok=False)
                    command, env = sandbox.command(
                        settings,
                        engine="markitdown",
                        mode="probe",
                        source=None,
                        output=output,
                        suffix=".txt",
                    )
                    _supervise(command, env, directory, lambda: False, timeout=8)
                    ready = read_result(output).get("ready") is True
            except (ConversionError, OSError, ValueError, sandbox.SandboxUnavailable):
                ready = False
            _availability_cache.clear()
            _availability_cache[key] = (time.monotonic(), ready)
            if not ready:
                raise ConversionError(sandbox.UNAVAILABLE)
    except (OSError, ValueError, sandbox.SandboxUnavailable) as exc:
        raise ConversionError(sandbox.UNAVAILABLE) from exc
