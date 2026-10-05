import asyncio
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .conversion import ConversionError, run_conversion
from .preview import render_preview as render_preview

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
MAX_BODY_BYTES = MAX_TOTAL_BYTES + 1024 * 1024
MAX_FILES = 10
EXTENSIONS = (".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv", ".json")
STATIC = Path(__file__).parent / "static"
BODY_LIMIT_ERROR = "请求体超过 51 MiB 限制"
_conversion_slots = asyncio.Semaphore(2)


class LocalRequestMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])

        async def safe_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"].extend(
                    [
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"x-frame-options", b"DENY"),
                        (
                            b"content-security-policy",
                            b"default-src 'none'; script-src 'self'; style-src 'self'; "
                            b"connect-src 'self'; img-src 'none'; font-src 'none'; "
                            b"base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
                        ),
                    ]
                )
            await send(message)

        async def reject(status: int, detail: str) -> None:
            await JSONResponse({"detail": detail}, status_code=status)(
                scope, receive, safe_send
            )

        content_length = headers.get(b"content-length")
        if content_length:
            try:
                length = int(content_length)
                if length < 0:
                    raise ValueError
            except ValueError:
                await reject(400, "无效的请求长度")
                return
            if length > MAX_BODY_BYTES:
                await reject(413, BODY_LIMIT_ERROR)
                return

        if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            origin = headers.get(b"origin")
            if origin:
                try:
                    parsed = urlsplit(origin.decode("ascii"))
                    same_origin = (
                        parsed.scheme == scope["scheme"]
                        and parsed.netloc.lower()
                        == headers.get(b"host", b"").decode("ascii").lower()
                        and not parsed.path
                        and not parsed.query
                        and not parsed.fragment
                    )
                except (UnicodeError, ValueError):
                    same_origin = False
                if not same_origin:
                    await reject(403, "仅允许从本机工作台发起转换")
                    return
            if headers.get(b"x-markitdown-request") != b"1":
                await reject(403, "缺少本机请求校验标记")
                return

        received = 0

        async def bounded_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_BODY_BYTES:
                    # Multipart parser closes its temporary files for this exception.
                    raise MultiPartException(BODY_LIMIT_ERROR)
            return message

        await self.app(scope, bounded_receive, safe_send)


app = FastAPI(title="MarkItDown 本机工作台", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"]
)
app.add_middleware(LocalRequestMiddleware)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def safe_filename(raw: str | None) -> str:
    name = (raw or "document").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(
        char
        for char in name
        if not unicodedata.category(char).startswith("C") and char not in '<>:"|?*'
    ).strip(" .")
    suffix = Path(name).suffix.lower()
    stem = name[: -len(suffix)] if suffix else name
    return (stem[:160].strip(" .") or "document") + suffix[:16]


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html", media_type="text/html")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
async def config() -> dict:
    return {
        "max_file_bytes": MAX_FILE_BYTES,
        "max_total_bytes": MAX_TOTAL_BYTES,
        "max_files": MAX_FILES,
        "extensions": list(EXTENSIONS),
    }


async def convert_upload(upload: UploadFile) -> dict:
    filename = safe_filename(upload.filename)
    result = {"filename": filename, "markdown": "", "html": "", "error": None}
    suffix = Path(filename).suffix.lower()
    try:
        if suffix not in EXTENSIONS:
            raise ConversionError("暂不支持此格式，请上传 PDF、DOCX、XLSX 或文本文件")
        if upload.size is not None and upload.size > MAX_FILE_BYTES:
            raise ConversionError("单个文件不能超过 20 MiB")
        with tempfile.TemporaryDirectory(prefix="markitdown-web-") as temporary:
            path = Path(temporary) / ("document" + suffix)
            size = 0
            with path.open("wb") as target:
                while chunk := await upload.read(64 * 1024):
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise ConversionError("单个文件不能超过 20 MiB")
                    target.write(chunk)
            if not size:
                raise ConversionError("文件为空，请选择有内容的文档")
            async with _conversion_slots:
                markdown, html = await asyncio.to_thread(run_conversion, path, suffix)
            result.update(markdown=markdown, html=html)
    except ConversionError as exc:
        result["error"] = str(exc)
    except Exception:
        result["error"] = "转换失败，请检查文档后重试"
    return result


@app.post("/api/convert")
async def convert(request: Request) -> dict:
    try:
        async with request.form(max_files=MAX_FILES, max_fields=0) as form:
            files = form.getlist("files")
            if (
                not files
                or len(files) > MAX_FILES
                or any(not isinstance(file, UploadFile) for file in files)
                or any(key != "files" for key in form)
            ):
                raise HTTPException(400, "请上传 1–10 个文件，字段名为 files")
            uploads: list[UploadFile] = files  # type: ignore[assignment]
            if sum(upload.size or 0 for upload in uploads) > MAX_TOTAL_BYTES:
                raise HTTPException(413, "一批文件总大小不能超过 50 MiB")
            # Keep results in upload order. A failed document never hides good results.
            results = [await convert_upload(upload) for upload in uploads]
            return {"results": results}
    except StarletteHTTPException as exc:
        if exc.detail == BODY_LIMIT_ERROR:
            raise HTTPException(413, BODY_LIMIT_ERROR) from exc
        raise
    except MultiPartException as exc:
        status = 413 if str(exc) == BODY_LIMIT_ERROR else 400
        raise HTTPException(status, "上传请求无效或超过大小限制") from exc
