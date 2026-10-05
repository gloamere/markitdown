"""Bounded, disposable conversion workers. This is not an OS security sandbox."""

import json
import os
import subprocess
import sys
from pathlib import Path

MAX_MARKDOWN_BYTES = 2 * 1024 * 1024
CONVERSION_TIMEOUT = 45
MAX_HTML_BYTES = 4 * 1024 * 1024
MAX_RESULT_BYTES = 16 * 1024 * 1024


class ConversionError(Exception):
    """A safe, user-facing conversion error."""


def run_conversion(path: Path, suffix: str) -> tuple[str, str]:
    output = path.parent / "result.json"
    # Do not pass API keys, credentials, proxy settings or the user's home to parsers.
    env = {
        "PATH": os.defpath,
        "HOME": str(path.parent),
        "TMPDIR": str(path.parent),
        "TEMP": str(path.parent),
        "TMP": str(path.parent),
        "PYTHONUTF8": "1",
        "ORT_DISABLE_TELEMETRY": "1",
        "MARKITDOWN_PARENT_PID": str(os.getpid()),
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
    }
    # SystemRoot is required by Python on Windows, if used locally there.
    if "SystemRoot" in os.environ:
        env["SystemRoot"] = os.environ["SystemRoot"]
    try:
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "markitdown_web.worker",
                str(path),
                suffix,
                str(output),
            ],
            cwd=path.parent,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=CONVERSION_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ConversionError("转换超时（45 秒），请拆分文档后重试") from exc
    if process.returncode != 0 or not output.is_file():
        raise ConversionError("无法转换此文件，可能已损坏、加密或超出资源限制")
    if output.stat().st_size > MAX_RESULT_BYTES:
        raise ConversionError("转换结果过大，请拆分文档后重试")
    result = json.loads(output.read_text(encoding="utf-8"))
    if result.get("error"):
        raise ConversionError(result["error"])
    markdown = result["markdown"]
    if (
        not isinstance(markdown, str)
        or len(markdown.encode("utf-8")) > MAX_MARKDOWN_BYTES
    ):
        raise ConversionError("转换结果超过 2 MiB，请拆分文档后重试")
    html = result["html"]
    if not isinstance(html, str) or len(html.encode("utf-8")) > MAX_HTML_BYTES:
        raise ConversionError("预览结果超过 4 MiB，请拆分文档后重试")
    return markdown, html
