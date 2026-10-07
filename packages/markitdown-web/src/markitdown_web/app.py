"""Invitation-only API. This release still binds to loopback for local evaluation."""

import asyncio
import secrets
import sqlite3
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __version__
from .auth import AuthError, AuthService
from .distribution import downloads
from .engines import engine_config
from .governance import GovernanceError
from .jobs import JobError, JobService
from .preview import render_preview as render_preview
from .state import Database, Settings
from .state import safe_filename as safe_filename

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
MAX_BODY_BYTES = MAX_TOTAL_BYTES + 1024 * 1024
MAX_JSON_BYTES = 64 * 1024
MAX_FILES = 10
EXTENSIONS = (".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv", ".json")
STATIC = Path(__file__).parent / "static"
BODY_LIMIT_ERROR = "请求体超过大小限制"
SESSION_COOKIE = "markitdown_session"


class LocalRequestMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        scope.setdefault("state", {})["request_id"] = uuid.uuid4().hex
        limit = MAX_BODY_BYTES if scope["path"] == "/api/jobs" else MAX_JSON_BYTES

        async def safe_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                user_id = scope.get("state", {}).get("user_id")
                if user_id:
                    message["headers"].append(
                        (b"x-markitdown-user", user_id.encode("ascii"))
                    )
                message["headers"].extend(
                    [
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"x-frame-options", b"DENY"),
                        (b"x-request-id", scope["state"]["request_id"].encode("ascii")),
                        (
                            b"content-security-policy",
                            b"default-src 'none'; script-src 'self'; style-src 'self'; "
                            b"connect-src 'self'; "
                            + (
                                b"img-src 'self'; "
                                if scope["path"] == "/"
                                else b"img-src 'none'; "
                            )
                            + b"font-src 'none'; "
                            b"base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
                        ),
                    ]
                )
                if self.settings.deployment_mode == "production":
                    message["headers"].append(
                        (b"strict-transport-security", b"max-age=31536000")
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
            if length > limit:
                await reject(413, BODY_LIMIT_ERROR)
                return

        if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            origin = headers.get(b"origin")
            if origin:
                try:
                    parsed = urlsplit(origin.decode("ascii"))
                    same_origin = (
                        (
                            origin.decode("ascii") == self.settings.public_origin
                            if self.settings.deployment_mode == "production"
                            else parsed.scheme == scope["scheme"]
                            and parsed.netloc.lower()
                            == headers.get(b"host", b"").decode("ascii").lower()
                        )
                        and not parsed.path
                        and not parsed.query
                        and not parsed.fragment
                    )
                except (UnicodeError, ValueError):
                    same_origin = False
                if not same_origin:
                    await reject(403, "仅允许从同源工作台发起请求")
                    return
            if headers.get(b"x-markitdown-request") != b"1":
                await reject(403, "缺少请求校验标记")
                return

        received = 0

        async def bounded_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    if scope["path"] != "/api/jobs":
                        raise HTTPException(413, BODY_LIMIT_ERROR)
                    # Multipart parser closes its temporary files on this exception.
                    raise MultiPartException(BODY_LIMIT_ERROR)
            return message

        await self.app(scope, bounded_receive, safe_send)


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(InputModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class RegisterInput(InputModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    invite_token: str = Field(min_length=16, max_length=128)


class InviteInput(InputModel):
    ttl_hours: StrictInt = Field(default=24, ge=1, le=168)


class UserLimitsInput(InputModel):
    daily_quota: StrictInt | None = Field(default=None, ge=1, le=1000)
    max_file_bytes: StrictInt | None = Field(
        default=None, ge=1024 * 1024, le=MAX_FILE_BYTES
    )
    is_active: StrictBool | None = None


class ArchiveInput(InputModel):
    job_ids: list[str] = Field(min_length=1, max_length=10)


class BusinessChanges(InputModel):
    default_daily_quota: StrictInt | None = Field(default=None, ge=1, le=1000)
    default_max_file_bytes: StrictInt | None = Field(
        default=None, ge=1024 * 1024, le=MAX_FILE_BYTES
    )
    retention_seconds: StrictInt | None = Field(default=None, ge=3600, le=604800)


class BusinessSettingsInput(InputModel):
    expected_version: StrictInt = Field(ge=1)
    changes: BusinessChanges


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        # No accounts or secrets are created automatically. Bootstrap is interactive.
        database = Database(settings)
        application.state.db = database
        application.state.upload_slots = asyncio.Semaphore(2)
        application.state.auth_slots = asyncio.Semaphore(4)
        application.state.auth = AuthService(database, settings)
        application.state.jobs = JobService(
            database, settings, application.state.auth.governance.current
        )
        if settings.start_workers:
            await asyncio.to_thread(application.state.jobs.start)
        try:
            yield
        finally:
            await asyncio.to_thread(application.state.jobs.stop)

    application = FastAPI(
        title="MarkItDown 邀请制工作台",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.settings = settings
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=(
            [str(urlsplit(settings.public_origin or "").hostname)]
            if settings.deployment_mode == "production"
            else ["localhost", "127.0.0.1", "[::1]"]
        ),
    )
    application.add_middleware(LocalRequestMiddleware, settings=settings)
    application.mount("/static", StaticFiles(directory=STATIC), name="static")

    @application.exception_handler(AuthError)
    @application.exception_handler(JobError)
    @application.exception_handler(GovernanceError)
    async def service_error(
        request: Request, exc: AuthError | JobError | GovernanceError
    ):
        payload = {"detail": exc.detail}
        if code := getattr(exc, "code", None):
            payload["code"] = code
        return JSONResponse(
            payload,
            status_code=exc.status_code,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse({"detail": "请求参数不符合要求"}, status_code=422)

    @application.exception_handler(MultiPartException)
    async def multipart_error(request: Request, exc: MultiPartException):
        code = 413 if str(exc) == BODY_LIMIT_ERROR else 400
        return JSONResponse({"detail": "上传请求无效或超过大小限制"}, status_code=code)

    @application.exception_handler(sqlite3.OperationalError)
    async def database_busy(request: Request, exc: sqlite3.OperationalError):
        return JSONResponse({"detail": "服务暂时繁忙，请稍后重试"}, status_code=503)

    def auth(request: Request) -> AuthService:
        return request.app.state.auth

    def jobs(request: Request) -> JobService:
        return request.app.state.jobs

    async def session(
        request: Request, *, mutate: bool = False, admin: bool = False
    ) -> dict:
        raw_token = request.cookies.get(SESSION_COOKIE, "")
        current = await asyncio.to_thread(auth(request).get_session, raw_token)
        if current is None:
            raise HTTPException(401, "请先登录")
        request.state.user_id = current["user"]["id"]
        if mutate:
            supplied = request.headers.get("X-CSRF-Token", "")
            if (
                len(supplied) != 64
                or any(char not in "0123456789abcdef" for char in supplied)
                or not secrets.compare_digest(supplied, current["csrf_token"])
            ):
                raise HTTPException(403, "请求校验失败，请刷新页面后重试")
        if admin and not current["user"]["is_admin"]:
            raise HTTPException(403, "需要管理员权限")
        return current

    async def session_payload(request: Request, current: dict) -> dict:
        request.state.user_id = current["user"]["id"]
        return {
            **current,
            "usage": await asyncio.to_thread(
                jobs(request).get_usage, current["user"]["id"]
            ),
        }

    def client_ip(request: Request) -> str:
        # Uvicorn is run with proxy_headers=False; never trust arbitrary X-Forwarded-For.
        return request.client.host if request.client else "unknown"

    @application.get("/app")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @application.get("/")
    async def website() -> FileResponse:
        return FileResponse(STATIC / "site.html", media_type="text/html")

    @application.get("/api/downloads")
    async def installer_downloads() -> dict:
        return await asyncio.to_thread(downloads, STATIC)

    @application.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    @application.get("/api/config")
    async def config(request: Request) -> dict:
        business = await asyncio.to_thread(auth(request).governance.current)
        return {
            "max_file_bytes": settings.max_file_bytes,
            "max_total_bytes": settings.max_total_bytes,
            "max_files": settings.max_files,
            "extensions": list(EXTENSIONS),
            "engines": await asyncio.to_thread(engine_config, settings),
            "retention_seconds": business["retention_seconds"],
            "default_daily_quota": business["default_daily_quota"],
            "default_max_file_bytes": business["default_max_file_bytes"],
            "history_seconds": settings.history_seconds,
            "password_min_length": 6,
            "application_version": __version__,
            "deployment_mode": settings.deployment_mode,
            "has_admin": await asyncio.to_thread(auth(request).has_admin),
        }

    @application.get("/api/me")
    async def me(request: Request) -> dict:
        return await session_payload(request, await session(request))

    @application.post("/api/auth/register", status_code=201)
    async def register(request: Request, body: RegisterInput) -> dict:
        slots = request.app.state.auth_slots
        if slots.locked():
            raise HTTPException(429, "登录或注册请求较多，请稍后重试")
        async with slots:
            user = await asyncio.to_thread(
                auth(request).register,
                body.username,
                body.password,
                body.invite_token,
                client_ip(request),
                request_id=request.state.request_id,
            )
        return {"user": user}

    @application.post("/api/auth/login")
    async def login(request: Request, body: LoginInput) -> JSONResponse:
        slots = request.app.state.auth_slots
        if slots.locked():
            raise HTTPException(429, "登录或注册请求较多，请稍后重试")
        async with slots:
            raw_token, current = await asyncio.to_thread(
                auth(request).login, body.username, body.password, client_ip(request)
            )
        previous = request.cookies.get(SESSION_COOKIE)
        if previous:
            await asyncio.to_thread(auth(request).logout, previous)
        response = JSONResponse(await session_payload(request, current))
        response.set_cookie(
            SESSION_COOKIE,
            raw_token,
            max_age=settings.session_seconds,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
            path="/",
        )
        return response

    @application.post("/api/auth/logout")
    async def logout(request: Request) -> JSONResponse:
        await session(request, mutate=True)
        await asyncio.to_thread(
            auth(request).logout, request.cookies.get(SESSION_COOKIE, "")
        )
        response = JSONResponse({"status": "ok"})
        response.delete_cookie(
            SESSION_COOKIE,
            path="/",
            secure=settings.cookie_secure,
            httponly=True,
            samesite="strict",
        )
        return response

    @application.get("/api/jobs")
    async def list_jobs(request: Request) -> dict:
        current = await session(request)
        user_id = current["user"]["id"]
        return {
            "jobs": await asyncio.to_thread(jobs(request).list_jobs, user_id),
            "usage": await asyncio.to_thread(jobs(request).get_usage, user_id),
        }

    @application.post("/api/jobs", status_code=202)
    async def submit_jobs(request: Request) -> dict:
        current = await session(request, mutate=True)
        slots = request.app.state.upload_slots
        if slots.locked():
            raise HTTPException(429, "同时上传人数较多，请稍后重试")
        async with slots:
            return await ingest_jobs(request, current)

    async def ingest_jobs(request: Request, current: dict) -> dict:
        user_id = current["user"]["id"]
        max_file_bytes = min(settings.max_file_bytes, current["user"]["max_file_bytes"])
        accepted: list[dict] = []
        errors: list[dict] = []
        try:
            async with request.form(max_files=settings.max_files, max_fields=1) as form:
                engines = form.getlist("engine")
                if len(engines) > 1 or any(
                    not isinstance(value, str) for value in engines
                ):
                    raise HTTPException(400, "请提供一个有效的转换引擎")
                engine = engines[0] if engines else "markitdown"
                if engine not in {"markitdown", "docling"}:
                    raise HTTPException(400, "不支持此转换引擎")
                files = form.getlist("files")
                if (
                    not files
                    or len(files) > settings.max_files
                    or any(not isinstance(file, UploadFile) for file in files)
                    or any(key not in {"files", "engine"} for key in form)
                ):
                    raise HTTPException(400, "请上传 1–10 个文件，字段名为 files")
                uploads: list[UploadFile] = files  # type: ignore[assignment]
                if (
                    sum(upload.size or 0 for upload in uploads)
                    > settings.max_total_bytes
                ):
                    raise HTTPException(413, "一批文件总大小不能超过 50 MiB")
                read_total = 0
                for file_index, upload in enumerate(uploads):
                    filename = safe_filename(upload.filename)
                    suffix = Path(filename).suffix.lower()
                    if suffix not in EXTENSIONS:
                        errors.append(
                            {
                                "filename": filename,
                                "file_index": file_index,
                                "error": "暂不支持此文件格式",
                            }
                        )
                        continue
                    if upload.size is not None and upload.size > max_file_bytes:
                        errors.append(
                            {
                                "filename": filename,
                                "file_index": file_index,
                                "error": "文件超过当前账户的单文件大小限制",
                            }
                        )
                        continue
                    payload = bytearray()
                    while chunk := await upload.read(64 * 1024):
                        payload.extend(chunk)
                        read_total += len(chunk)
                        if (
                            len(payload) > max_file_bytes
                            or read_total > settings.max_total_bytes
                        ):
                            raise HTTPException(413, "上传文件超过大小限制")
                    if not payload:
                        errors.append(
                            {
                                "filename": filename,
                                "file_index": file_index,
                                "error": "文件为空",
                            }
                        )
                        continue
                    accepted.append(
                        {"filename": filename, "suffix": suffix, "data": bytes(payload)}
                    )
                if not accepted:
                    return {"jobs": [], "errors": errors}
                created = await asyncio.to_thread(
                    jobs(request).enqueue,
                    user_id,
                    accepted,
                    engine,
                    submission_key=request.headers.get("Idempotency-Key"),
                )
                return {"jobs": created, "errors": errors}
        except StarletteHTTPException as exc:
            if exc.detail == BODY_LIMIT_ERROR:
                raise HTTPException(413, BODY_LIMIT_ERROR) from exc
            raise

    # Static archive route must precede /{job_id} for unambiguous routing.
    @application.post("/api/jobs/archive")
    async def archive(request: Request, body: ArchiveInput) -> Response:
        current = await session(request, mutate=True)
        content = await asyncio.to_thread(
            jobs(request).archive, current["user"]["id"], body.job_ids
        )
        return Response(
            content,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="markitdown-batch.zip"'
            },
        )

    @application.get("/api/jobs/{job_id}")
    async def detail(request: Request, job_id: str) -> dict:
        current = await session(request)
        return await asyncio.to_thread(
            jobs(request).get_job, current["user"]["id"], job_id, include_content=True
        )

    @application.get("/api/jobs/{job_id}/download")
    async def download(request: Request, job_id: str) -> Response:
        current = await session(request)
        filename, content = await asyncio.to_thread(
            jobs(request).download, current["user"]["id"], job_id
        )
        return Response(
            content,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename=\"document.md\"; filename*=UTF-8''{quote(filename, safe='')}"
            },
        )

    @application.get("/api/jobs/{job_id}/manifest")
    async def manifest(request: Request, job_id: str) -> JSONResponse:
        current = await session(request)
        job = await asyncio.to_thread(
            jobs(request).get_job, current["user"]["id"], job_id
        )
        payload = {
            "manifest_version": 1,
            "job_id": job["id"],
            "filename": job["filename"],
            "source_sha256": job.get("source_sha256"),
            "engine": job.get("engine"),
            "status": job["status"],
            "submission_snapshot": job.get("submission_snapshot"),
            "attempt_history": job.get("attempt_history", []),
            "metadata": job.get("metadata", {}),
            "ocr_enabled": False,
            "content_quality": "not_assessed",
            "native_document_json": False,
        }
        return JSONResponse(
            payload,
            headers={
                "Content-Disposition": 'attachment; filename="conversion-manifest.json"'
            },
        )

    @application.post("/api/jobs/{job_id}/retry")
    async def retry(request: Request, job_id: str) -> dict:
        current = await session(request, mutate=True)
        return await asyncio.to_thread(
            jobs(request).retry,
            current["user"]["id"],
            job_id,
            submission_key=request.headers.get("Idempotency-Key"),
        )

    @application.post("/api/jobs/{job_id}/cancel")
    async def cancel(request: Request, job_id: str) -> dict:
        current = await session(request, mutate=True)
        return await asyncio.to_thread(
            jobs(request).cancel, current["user"]["id"], job_id
        )

    @application.delete("/api/jobs/{job_id}")
    async def delete_job(request: Request, job_id: str) -> dict:
        current = await session(request, mutate=True)
        await asyncio.to_thread(jobs(request).delete_job, current["user"]["id"], job_id)
        return {"status": "deleted"}

    @application.get("/api/admin/users")
    async def users(request: Request) -> dict:
        current = await session(request, admin=True)
        return {
            "users": await asyncio.to_thread(
                auth(request).list_users, current["user"]["id"]
            )
        }

    @application.patch("/api/admin/users/{user_id}")
    async def update_user(
        request: Request, user_id: str, body: UserLimitsInput
    ) -> dict:
        current = await session(request, mutate=True, admin=True)
        changes = body.model_dump(exclude_none=True)
        if not changes:
            raise HTTPException(400, "请提供要修改的额度或状态")
        return await asyncio.to_thread(
            auth(request).update_user,
            current["user"]["id"],
            user_id,
            **changes,
            request_id=request.state.request_id,
        )

    @application.get("/api/admin/invites")
    async def invites(request: Request) -> dict:
        current = await session(request, admin=True)
        return {
            "invites": await asyncio.to_thread(
                auth(request).list_invites, current["user"]["id"]
            )
        }

    @application.post("/api/admin/invites", status_code=201)
    async def create_invite(request: Request, body: InviteInput) -> dict:
        current = await session(request, mutate=True, admin=True)
        return await asyncio.to_thread(
            auth(request).create_invite,
            current["user"]["id"],
            ttl_seconds=body.ttl_hours * 3600,
            request_id=request.state.request_id,
        )

    @application.delete("/api/admin/invites/{invite_id}")
    async def revoke_invite(request: Request, invite_id: str) -> dict:
        current = await session(request, mutate=True, admin=True)
        await asyncio.to_thread(
            auth(request).revoke_invite,
            current["user"]["id"],
            invite_id,
            request_id=request.state.request_id,
        )
        return {"status": "revoked"}

    @application.get("/api/admin/settings")
    async def business_settings(request: Request) -> dict:
        current = await session(request, admin=True)
        result = await asyncio.to_thread(
            auth(request).governance.get_settings, current["user"]["id"]
        )
        result["maintenance"] = await asyncio.to_thread(jobs(request).cleanup_status)
        return result

    @application.patch("/api/admin/settings")
    async def update_business_settings(
        request: Request, body: BusinessSettingsInput
    ) -> dict:
        current = await session(request, mutate=True, admin=True)
        return await asyncio.to_thread(
            auth(request).governance.update_settings,
            current["user"]["id"],
            body.changes.model_dump(exclude_none=True),
            expected_version=body.expected_version,
            request_id=request.state.request_id,
        )

    @application.get("/api/admin/audit")
    async def audit_events(request: Request) -> dict:
        current = await session(request, admin=True)
        return {
            "events": await asyncio.to_thread(
                auth(request).governance.list_audit, current["user"]["id"]
            )
        }

    return application


app = create_app()
