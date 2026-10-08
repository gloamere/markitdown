"""One document per process; no plugins, URL fetching, OCR or AI services."""

import io
import json
import os
import socket
import sys
import zipfile
from pathlib import Path, PurePosixPath

from .conversion import (
    MAX_MARKDOWN_BYTES,
    MAX_RESULT_BYTES,
    SAFE_ERRORS,
    ConversionError,
)

MAX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 2000


def apply_limits() -> None:
    # On Linux, an abrupt service crash must not leave a parser running after
    # its queue slot is recovered by a new service process.
    expected_parent = os.environ.get("MARKITDOWN_PARENT_PID")
    if sys.platform == "linux" and expected_parent:
        import ctypes
        import signal

        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
            raise RuntimeError("Cannot enforce parser parent-death cleanup")
        if os.getppid() != int(expected_parent):
            os._exit(1)
    # Production fails closed; local development retains its explicitly weaker
    # platform-dependent limit behavior. Namespace cleanup is handled by bwrap.
    secured = os.environ.get("MARKITDOWN_SANDBOX_PROFILE") == "linux-bwrap-v1"
    if secured and sys.platform != "linux":
        raise RuntimeError("Linux production worker required")
    # Best-effort resource controls only outside the production profile.
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (35, 35))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_RESULT_BYTES,) * 2)
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        if secured:
            resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))
    except (ImportError, ValueError, OSError):
        if secured:
            raise
    if secured:
        from .sandbox import enforce_worker_filter

        enforce_worker_filter()

    # Defense in depth for supported Python converters, not a network namespace.
    def deny_network(*args, **kwargs):
        raise OSError("Network is disabled in the document worker")

    socket.socket.connect = deny_network  # type: ignore[method-assign]
    socket.socket.connect_ex = deny_network  # type: ignore[method-assign,assignment]
    socket.create_connection = deny_network  # type: ignore[assignment]
    socket.getaddrinfo = deny_network  # type: ignore[assignment]


def validate_office(data: bytes, suffix: str) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            names = [item.filename for item in members]
            required = "word/document.xml" if suffix == ".docx" else "xl/workbook.xml"
            if required not in names or "[Content_Types].xml" not in names:
                raise ConversionError("文件内容与扩展名不符，请上传有效的 Office 文档")
            if len(names) != len(set(names)):
                raise ConversionError("文档包含重复的压缩条目，已拒绝处理")
            if (
                len(members) > MAX_ARCHIVE_MEMBERS
                or sum(item.file_size for item in members) > MAX_EXPANDED_BYTES
            ):
                raise ConversionError("文档解压后过大或结构过于复杂，请拆分后重试")
            for item in members:
                name = item.filename
                if (
                    item.flag_bits & 1
                    or "\\" in name
                    or ":" in name
                    or name.startswith("/")
                    or ".." in PurePosixPath(name).parts
                    or (item.external_attr >> 16) & 0o170000 == 0o120000
                    or item.file_size > 25 * 1024 * 1024
                    or (
                        item.file_size > 1024 * 1024
                        and item.file_size / max(item.compress_size, 1) > 200
                    )
                ):
                    raise ConversionError("文档包含不安全或过大的压缩条目，已拒绝处理")
                # Relationships can point to XML with any extension, including .bin.
                content = archive.read(item)
                normalized = content.replace(b"\x00", b"").upper()
                if b"<!DOCTYPE" in normalized or b"<!ENTITY" in normalized:
                    raise ConversionError("文档包含不安全的 XML 声明，已拒绝处理")
    except zipfile.BadZipFile as exc:
        raise ConversionError("Office 文档已损坏或文件内容与扩展名不符") from exc


def convert_document(path: Path, suffix: str) -> str:
    # Import after resource/network restrictions have been applied.
    # The process-lifetime env opt-out handles initialization; the API also
    # disables subsequent events on platforms with a different telemetry provider.
    import onnxruntime

    onnxruntime.disable_telemetry_events()

    from markitdown import StreamInfo
    from markitdown.converters import (
        CsvConverter,
        DocxConverter,
        PdfConverter,
        PlainTextConverter,
        XlsxConverter,
    )

    data = path.read_bytes()
    if suffix in {".docx", ".xlsx"}:
        validate_office(data, suffix)
    elif suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise ConversionError("文件内容与扩展名不符，请上传有效的 PDF")
    elif b"\x00" in data[:8192] and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ConversionError("此文件不是可识别的文本文件")

    converters = {
        ".pdf": PdfConverter,
        ".docx": DocxConverter,
        ".xlsx": XlsxConverter,
        ".csv": CsvConverter,
        ".txt": PlainTextConverter,
        ".md": PlainTextConverter,
        ".json": PlainTextConverter,
    }
    converter = converters[suffix]()
    markdown = converter.convert(
        io.BytesIO(data), StreamInfo(extension=suffix)
    ).markdown
    if len(markdown.encode("utf-8")) > MAX_MARKDOWN_BYTES:
        raise ConversionError("转换结果超过 2 MiB，请拆分文档后重试")
    if not markdown.strip():
        raise ConversionError("没有提取到文字；扫描版 PDF 需要 OCR，当前版本暂不支持")
    return markdown


def main() -> None:
    apply_limits()
    path, suffix, output = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    result: dict[str, object]
    metadata: dict[str, str] | None = None
    try:
        if os.environ.get("MARKITDOWN_WORKER_MODE") == "probe":
            import importlib.util

            ready = all(
                importlib.util.find_spec(name) is not None
                for name in ("markitdown", "onnxruntime", "markdown_it", "bleach")
            )
            result = {"ready": ready}
        else:
            import importlib.metadata

            metadata = {
                "version": importlib.metadata.version("markitdown"),
                "python": sys.version.split()[0],
            }
            markdown = convert_document(path, suffix)
            result = {"markdown": markdown, "error": None}
    except ConversionError as exc:
        code = next(
            (key for key, value in SAFE_ERRORS.items() if value == str(exc)),
            "conversion_failed",
        )
        result = {"error": code}
    except Exception:
        # Never return parser tracebacks, file contents or filesystem paths to clients.
        result = {"error": "conversion_failed"}
    if metadata is not None:
        result["metadata"] = metadata
    output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
