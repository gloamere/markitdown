"""Opt-in, real Chromium regression gate using disposable synthetic accounts.

This is deliberately not a pytest-collected module. See docs/BROWSER-TESTS.md.
--self-check never imports Playwright/app modules, opens sockets, or runs a browser.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
import zipfile
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
PLAYWRIGHT_VERSION = "1.63.0"
USER_PASSWORD = "Synt6!"  # Exactly six characters; only disposable test accounts.
FIRST_MARKER = "SYNTHETIC_FIRST_37_25"
SECOND_MARKER = "SYNTHETIC_SECOND_82_50"
STEPS = (
    "sandboxed_browser_and_isolated_server",
    "admin_login_and_keyboard_auth_tabs",
    "invitation_ttl_and_six_character_registration",
    "real_file_upload_repeated_click_and_preview",
    "source_compare_keyboard_and_clipboard_fallback",
    "markdown_zip_and_manifest_downloads",
    "cross_owner_and_non_admin_denial",
    "reload_back_and_newer_selection",
    "admin_actual_values_version_conflict_and_retention",
    "account_controls_self_disable_and_audit",
    "logout_stale_response_and_back_privacy",
    "mobile_layout_keyboard_and_screenshots",
    "keyboard_login_choose_remove_convert_read_and_export",
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def chooser_diagnostics(data: dict) -> dict:
    """Only bounded event counts and readiness booleans can enter public logs."""
    fields = {
        "focused",
        "chooser_enabled",
        "input_enabled",
        "event_seen",
        "enter_count",
        "chooser_enter_count",
        "trusted_enter_count",
        "chooser_click_count",
        "input_click_count",
    }
    return {
        key: value
        for key, value in data.items()
        if key in fields
        and (type(value) is bool or type(value) is int and 0 <= value <= 100)
    }


def public_failure(error: BaseException) -> dict:
    """Public CI logs contain locations/categories, never page values or files."""
    detail = str(error)
    category = (
        "sandbox_unavailable"
        if "No usable sandbox" in detail or "sandboxing failed" in detail
        else "timeout"
        if "Timeout" in type(error).__name__
        else "assertion_failed"
        if isinstance(error, AssertionError)
        else "execution_failed"
    )
    locations = [
        {"function": frame.name, "line": frame.lineno}
        for frame in traceback.extract_tb(error.__traceback__)
        if Path(frame.filename).name == Path(__file__).name
    ]
    result = {
        "type": type(error).__name__,
        "category": category,
        "locations": locations,
    }
    diagnostics = getattr(error, "keyboard_chooser_diagnostics", None)
    if isinstance(diagnostics, dict):
        result["keyboard_chooser"] = chooser_diagnostics(diagnostics)
    return result


def artifact_path(root: Path, relative: str) -> Path:
    """Use only harness-selected relative destinations, never download filenames."""
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise ValueError("Artifact destination must stay within its run directory")
    path = root / candidate
    within_root = [
        root,
        *(
            root.joinpath(*candidate.parts[:i])
            for i in range(1, len(candidate.parts) + 1)
        ),
    ]
    if any(part.is_symlink() for part in within_root):
        raise ValueError("Symlinks are not valid artifact destinations")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Artifact destination escaped the run directory")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def prepare_output(path: Path) -> Path:
    path = path.expanduser().absolute()
    if path.is_symlink():
        raise ValueError("Output cannot be a symlink")
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError(
            "Use a new or empty output directory; prior evidence is preserved"
        )
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path.resolve()


def write_fixtures(directory: Path) -> tuple[Path, Path]:
    first = directory / "合成-预算.md"
    second = directory / "合成-第二份.txt"
    first.write_text(
        f"# Synthetic browser fixture\n\n{FIRST_MARKER}\n\n"
        "| Item | Value |\n| --- | ---: |\n| 茶 & coffee | 37.25 |\n\n"
        "Unicode: 中文 ✓. Literal input below must never execute in preview.\n\n"
        "<script>window.syntheticDocumentExecuted = true</script>\n\n"
        "![no image](https://example.invalid/synthetic-image.png)\n\n"
        "[no active link](https://example.invalid/synthetic-target)\n\n"
        + "unbroken_" * 45
        + "\n",
        encoding="utf-8",
    )
    second.write_text(f"Second synthetic document\n{SECOND_MARKER}\n", encoding="utf-8")
    return first, second


class Evidence:
    def __init__(self, output: Path):
        self.output = output
        self.data = {
            "schema_version": 1,
            "kind": "synthetic_real_browser_regression",
            "started_at": now(),
            "finished_at": None,
            "automated_status": "not_run",
            "visual_review_status": "not_reviewed",
            "release_gate_complete": False,
            "scope": "Local evaluation mode, real Chromium, synthetic accounts/files",
            "limits": [
                "Screenshots require separate human visual review",
                "No production sandbox, host/TLS, Docling model or real-user acceptance",
                "No assertion that native clipboard write succeeds on every platform",
                "File chooser bytes are fixture injection, not native dialog acceptance",
                "No manual screen-reader or native keyboard-zoom acceptance",
            ],
            "platform": platform.platform(),
            "python": platform.python_version(),
            "browser": None,
            "checks": [{"name": name, "status": "not_run"} for name in STEPS],
            "screenshots": [],
            "downloads": [],
            "page_errors": [],
            "unexpected_external_requests": [],
            "timing_injections": [],
        }
        try:
            self.data["git_commit"] = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip()
            self.data["git_dirty"] = bool(
                subprocess.check_output(
                    ["git", "status", "--porcelain"], cwd=ROOT, text=True
                ).strip()
            )
        except (OSError, subprocess.CalledProcessError):
            self.data["git_commit"] = None
            self.data["git_dirty"] = None
        self.flush()

    def flush(self) -> None:
        artifact_path(self.output, "evidence.json").write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    @contextmanager
    def step(self, name: str):
        row = next(row for row in self.data["checks"] if row["name"] == name)
        row.update(status="running", started_at=now())
        self.flush()
        try:
            yield row
        except BaseException as error:
            row.update(status="failed", error=f"{type(error).__name__}: {error}")
            raise
        else:
            row["status"] = "passed"
            print(f"PASS {name}", flush=True)
        finally:
            row["finished_at"] = now()
            self.flush()

    def file_record(self, path: Path) -> dict:
        content = path.read_bytes()
        return {
            "path": str(path.relative_to(self.output)),
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    async def screenshot(self, page, label: str) -> None:
        await page.evaluate("document.fonts.ready")
        await page.evaluate(
            "() => new Promise(resolve => requestAnimationFrame(resolve))"
        )
        dimensions = await page.evaluate(
            """() => ({viewport: innerWidth, scroll: Math.max(
                document.documentElement.scrollWidth, document.body.scrollWidth),
                height: innerHeight, device_scale_factor: devicePixelRatio})"""
        )
        path = artifact_path(self.output, f"screenshots/{label}.png")
        await page.evaluate("window.scrollTo(0, 0)")
        await page.screenshot(path=str(path), full_page=True, animations="disabled")
        require(
            path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), "Screenshot is not PNG"
        )
        self.data["screenshots"].append(
            {**self.file_record(path), **dimensions, "visual_review": "pending"}
        )
        self.flush()
        require(
            dimensions["scroll"] <= dimensions["viewport"] + 1,
            f"Horizontal page overflow in {label}: {dimensions}",
        )


@asynccontextmanager
async def local_server(directory: Path, password: str):
    """Own a fresh DB and reserved loopback socket; never target a running service."""
    import uvicorn

    from markitdown_web.app import create_app
    from markitdown_web.auth import AuthService
    from markitdown_web.state import Database, Settings

    settings = Settings(
        data_dir=directory,
        deployment_mode="local",
        public_origin=None,
        cookie_secure=False,
        docling_enabled=False,
        docling_python=None,
        docling_models=None,
        sandbox_runtime_root=None,
        start_workers=True,
    )
    # This is synthetic test setup only. No application bootstrap behavior changes.
    AuthService(Database(settings), settings).bootstrap_admin(
        "synthetic-admin", password
    )
    app = create_app(settings)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server = None
    task = None
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        host, port = listener.getsockname()
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=host,
                port=port,
                proxy_headers=False,
                access_log=False,
                log_level="warning",
                timeout_graceful_shutdown=10,
            )
        )
        task = asyncio.create_task(server.serve(sockets=[listener]))
        async with asyncio.timeout(30):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Uvicorn exited before startup")
                await asyncio.sleep(0.05)
        yield f"http://127.0.0.1:{port}"
    finally:
        if server is not None:
            server.should_exit = True
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=20)
            except asyncio.TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise RuntimeError("Synthetic test server did not shut down cleanly")
        listener.close()


async def api(page, path: str, method: str = "GET", body=None) -> dict:
    """Fetch in the actual authenticated browser, including real CSRF/Origin checks."""
    if not path.startswith("/api/") or "?" in path or "#" in path:
        raise ValueError("Tests only request explicit same-origin API paths")
    return await page.evaluate(
        """async ({path, method, body}) => {
            const headers = {'X-MarkItDown-Request': '1'};
            if (method !== 'GET') {
                const session = await fetch('/api/me', {cache: 'no-store'});
                if (session.ok) headers['X-CSRF-Token'] = (await session.json()).csrf_token;
                if (body !== null) headers['Content-Type'] = 'application/json';
            }
            const response = await fetch(path, {method, headers, cache: 'no-store',
                credentials: 'same-origin', ...(body === null ? {} : {body: JSON.stringify(body)})});
            const text = await response.text();
            let data; try {data = JSON.parse(text);} catch {data = text;}
            return {status: response.status, data, no_store: response.headers.get('cache-control')};
        }""",
        {"path": path, "method": method, "body": body},
    )


async def api_ok(page, path: str) -> dict:
    result = await api(page, path)
    require(result["status"] == 200, f"GET {path} returned {result['status']}")
    require("no-store" in (result["no_store"] or ""), f"Missing no-store: {path}")
    return result["data"]


async def login(page, username: str, password: str, expect) -> None:
    await expect(page.locator("#login-button")).to_be_enabled()
    await page.locator("#login-username").fill(username)
    await page.locator("#login-password").fill(password)
    await page.locator("#login-password").press("Enter")
    await expect(page.locator("#session-section")).to_be_visible()
    await expect(page.locator("#account-name")).to_have_text(username)
    await expect(page.locator("#login-password")).to_have_value("")


async def open_admin(page, expect) -> None:
    await page.locator("#admin-toggle").click()
    await expect(page.locator("#admin-panel")).to_be_visible()
    await expect(page.locator("#settings-save")).to_be_enabled()
    await expect(page.locator("#admin-refresh")).to_be_enabled()


async def invitation(
    page, expect, hours: int = 1, repeated: bool = False
) -> tuple[str, dict]:
    await page.locator("#invite-hours").select_option(str(hours))
    existing = len((await api_ok(page, "/api/admin/invites"))["invites"])
    before = time.time()
    async with page.expect_response(
        lambda r: urlsplit(r.url).path == "/api/admin/invites"
        and r.request.method == "POST"
    ) as event:
        if repeated:
            await page.locator("#invite-create").dblclick(delay=120)
        else:
            await page.locator("#invite-create").click()
    response = await event.value
    require(response.status == 201, "Invitation creation failed")
    data = await response.json()
    require(
        before + hours * 3600 <= data["expires_at"] <= time.time() + hours * 3600,
        "Invitation expiry does not match the selected TTL",
    )
    await expect(page.locator("#new-invite-token")).to_have_value(data["token"])
    await expect(page.locator("#invite-expiry")).to_contain_text("一次性")
    await expect(page.locator("#invite-create")).to_be_disabled()
    require(
        len((await api_ok(page, "/api/admin/invites"))["invites"]) == existing + 1,
        "Repeated invite gesture created multiple invitations",
    )
    await page.locator("#invite-dismiss").click()
    await expect(page.locator("#invite-create")).to_be_enabled()
    await expect(page.locator("#new-invite-token")).to_have_value("")
    return data["token"], data


async def register(page, username: str, token: str, expect) -> None:
    await expect(page.locator("#login-button")).to_be_enabled()
    await page.locator("#login-tab").focus()
    await page.keyboard.press("ArrowRight")
    await expect(page.locator("#register-tab")).to_be_focused()
    await expect(page.locator("#register-form")).to_be_visible()
    await page.locator("#register-username").fill(username)
    await page.locator("#register-password").fill(USER_PASSWORD)
    await page.locator("#invite-token").fill(token)
    async with page.expect_response(
        lambda r: urlsplit(r.url).path == "/api/auth/register"
    ) as event:
        await page.locator("#register-button").click()
    require((await event.value).status == 201, "Six-character registration failed")
    await expect(page.locator("#login-form")).to_be_visible()
    await expect(page.locator("#auth-message")).to_contain_text("账户已创建")
    await expect(page.locator("#register-password")).to_have_value("")
    await expect(page.locator("#invite-token")).to_have_value("")
    await login(page, username, USER_PASSWORD, expect)


async def upload(page, path: Path, expect, repeated: bool = False) -> dict:
    before = await api_ok(page, "/api/me")
    await page.locator("#file-input").set_input_files(str(path))
    await expect(page.locator("#queue-count")).to_have_text("1")
    async with page.expect_response(
        lambda r: urlsplit(r.url).path == "/api/jobs" and r.request.method == "POST"
    ) as event:
        if repeated:
            await page.locator("#convert-button").dblclick()
        else:
            await page.locator("#convert-button").click()
    response = await event.value
    require(response.status == 202, "Upload was not accepted")
    payload = await response.json()
    require(
        len(payload["jobs"]) == 1 and not payload["errors"], "Unexpected upload result"
    )
    job = payload["jobs"][0]
    await expect(page.locator("#queue-count")).to_have_text("0")
    await expect(page.locator("#download-button")).to_be_enabled(timeout=90000)
    await expect(page.locator("#document-name")).to_have_text(path.name)
    require(
        (await api_ok(page, "/api/me"))["usage"]["used"] == before["usage"]["used"] + 1,
        "Single accepted upload consumed the wrong quota",
    )
    completed = await api_ok(page, f"/api/jobs/{job['id']}")
    require(completed["status"] == "succeeded", "Real conversion did not succeed")
    return completed


async def confirm_click(
    page, selector: str, words: tuple[str, ...], accept=True
) -> str:
    captured = []

    async def handle(dialog):
        captured.append(dialog.message)
        if accept and all(word in dialog.message for word in words):
            await dialog.accept()
        else:
            await dialog.dismiss()

    page.once("dialog", handle)
    await page.locator(selector).click()
    require(bool(captured), "Expected a native confirmation dialog")
    require(
        all(word in captured[0] for word in words), "Confirmation omitted consequences"
    )
    return captured[0]


class DelayedDetail:
    """Delay a REAL same-origin response; never substitute a fake API result."""

    def __init__(self, page, origin: str, job_id: str, evidence: Evidence):
        self.page = page
        self.pattern = f"{origin}/api/jobs/{job_id}"
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.done = asyncio.Event()
        self.evidence = evidence
        self.failure = None

    async def route(self, route):
        try:
            response = await route.fetch()
            require(
                response.status == 200, "Delayed detail did not come from real server"
            )
            self.started.set()
            await self.release.wait()
            await route.fulfill(response=response)
        except Exception as error:
            self.failure = error
            self.started.set()
        finally:
            self.done.set()

    async def __aenter__(self):
        await self.page.route(self.pattern, self.route, times=1)
        return self

    async def wait(self):
        await asyncio.wait_for(self.started.wait(), 15)
        if self.failure:
            raise self.failure

    async def __aexit__(self, kind, value, tb):
        self.release.set()
        if self.started.is_set():
            await asyncio.wait_for(self.done.wait(), 15)
        await self.page.unroute(self.pattern, self.route)
        # An application-aborted request may make fulfilment fail. Only that
        # specific cancellation is expected; closed pages/browser errors fail.
        if self.failure and "net::ERR_ABORTED" not in str(self.failure):
            raise self.failure
        self.evidence.data["timing_injections"].append(
            {"kind": "held_real_detail", "application_aborted": bool(self.failure)}
        )


async def assert_private_cleared(page, expect) -> None:
    await expect(page.locator("#auth-section")).to_be_visible()
    await expect(page.locator("#session-section")).to_be_hidden()
    await expect(page.locator("#markdown-source")).to_have_value("")
    await expect(page.locator("#markdown-preview")).to_have_text("")
    await expect(page.locator("#history-list").locator("li")).to_have_count(0)
    await expect(page.locator("#new-invite-token")).to_have_value("")
    await expect(page.locator("#copy-button")).to_be_disabled()
    await expect(page.locator("#download-button")).to_be_disabled()
    require(
        await page.evaluate(
            "Object.keys(localStorage).length + Object.keys(sessionStorage).length"
        )
        == 0,
        "App persisted data in browser storage",
    )


async def save_download(
    page, selector: str, relative: str, evidence: Evidence, *, keyboard=False
) -> Path:
    async with page.expect_download() as event:
        if keyboard:
            require(
                await page.locator(selector).evaluate(
                    "el => el === document.activeElement"
                ),
                "Keyboard download control is not focused",
            )
            await page.keyboard.press("Enter")
        else:
            await page.locator(selector).click()
    download = await event.value
    require(await download.failure() is None, "Browser download failed")
    path = artifact_path(evidence.output, relative)
    await download.save_as(str(path))
    require(path.stat().st_size > 0, "Downloaded an empty artifact")
    evidence.data["downloads"].append(
        {
            **evidence.file_record(path),
            "suggested_filename": download.suggested_filename,
        }
    )
    evidence.flush()
    return path


async def tab_to(page, selector: str, expect, stops: list) -> None:
    """Find the next focus stop using only real Tab events, never focus()."""
    target = page.locator(selector)
    await expect(target).to_be_visible()
    for count in range(81):
        if await target.evaluate("el => el === document.activeElement"):
            await expect(target).to_be_focused()
            stops.append({"selector": selector, "tab_presses": count})
            return
        await page.keyboard.press("Tab")
    raise AssertionError(
        f"Keyboard could not reach {selector} within one bounded journey"
    )


async def keyboard_choose(page, expect, stops: list, evidence):
    """Arm interception before Tab traversal; one Enter must open the chooser."""
    # Observe only this synthetic gesture. Never record key text, inputs, labels,
    # document content, URLs, usernames, or credentials.
    await page.evaluate(
        """() => {
        const button = document.getElementById('choose-button');
        const input = document.getElementById('file-input');
        const counts = {enter_count: 0, chooser_enter_count: 0,
            trusted_enter_count: 0, chooser_click_count: 0, input_click_count: 0};
        const key = event => {
            if (event.key !== 'Enter') return;
            counts.enter_count++;
            if (event.target === button) counts.chooser_enter_count++;
            if (event.isTrusted) counts.trusted_enter_count++;
        };
        const click = event => {
            if (event.target === button || button.contains(event.target)) counts.chooser_click_count++;
            if (event.target === input) counts.input_click_count++;
        };
        document.addEventListener('keydown', key, true);
        document.addEventListener('click', click, true);
        window.__keyboardChooserProbe = {
            read: () => ({...counts, focused: document.activeElement === button,
                chooser_enabled: !button.disabled, input_enabled: !input.disabled}),
            dispose: () => {
                document.removeEventListener('keydown', key, true);
                document.removeEventListener('click', click, true);
                delete window.__keyboardChooserProbe;
            }
        };
    }"""
    )
    event_seen = False
    failure = None
    try:
        # Playwright 1.63 registers first event subscriptions without awaiting the
        # driver's interception setup. Arm before real Tab/readiness round trips,
        # not immediately before Enter. No delay, refocus, click, or retry.
        async with page.expect_file_chooser() as event:
            await tab_to(page, "#choose-button", expect, stops)
            await expect(page.locator("#choose-button")).to_be_enabled()
            await expect(page.locator("#file-input")).to_be_enabled()
            await expect(page.locator("#choose-button")).to_be_focused()
            await page.keyboard.press("Enter")
        chooser = await event.value
        event_seen = True
        return chooser
    except Exception as error:
        failure = error
        raise
    finally:
        try:
            diagnostics = chooser_diagnostics(
                await page.evaluate("() => window.__keyboardChooserProbe.read()")
            )
            diagnostics["event_seen"] = event_seen
            evidence.data["keyboard_chooser"] = diagnostics
            evidence.flush()
            if failure is not None:
                failure.keyboard_chooser_diagnostics = diagnostics
        except Exception:
            if failure is None:
                raise  # Diagnostic collection must not hide the original failure.
        finally:
            try:
                await page.evaluate("() => window.__keyboardChooserProbe.dispose()")
            except Exception:
                if failure is None:
                    raise


async def keyboard_journey(page, first_path, second_path, evidence, expect) -> dict:
    """All application interactions here use keyboard events. File bytes alone
    enter through Playwright's intercepted chooser, not an OS-dialog acceptance.
    """
    stops = []
    await page.goto("/app")
    await expect(page.locator("#login-button")).to_be_enabled()
    await page.keyboard.press("Tab")
    await expect(page.locator(".skip-link")).to_be_focused()
    await page.keyboard.press("Enter")
    await expect(page.locator("#main-content")).to_be_focused()
    await tab_to(page, "#login-tab", expect, stops)
    await page.keyboard.press("ArrowRight")
    await expect(page.locator("#register-tab")).to_be_focused()
    await page.keyboard.press("Home")
    await expect(page.locator("#login-tab")).to_be_focused()
    await tab_to(page, "#login-username", expect, stops)
    await page.keyboard.type("synthetic-reader")
    await tab_to(page, "#login-password", expect, stops)
    await page.keyboard.type(USER_PASSWORD)
    await tab_to(page, "#login-button", expect, stops)
    await page.keyboard.press("Enter")
    await expect(page.locator("#session-section")).to_be_visible()
    await expect(page.locator("#login-password")).to_have_value("")
    before = await api_ok(page, "/api/me")
    chooser = await keyboard_choose(page, expect, stops, evidence)
    gesture = evidence.data["keyboard_chooser"]
    require(
        all(
            gesture[field] == 1
            for field in [
                "enter_count",
                "chooser_enter_count",
                "trusted_enter_count",
                "chooser_click_count",
                "input_click_count",
            ]
        ),
        "Chooser did not follow one trusted keyboard gesture",
    )
    require(chooser.is_multiple(), "Keyboard chooser must allow multiple files")
    await chooser.set_files([str(first_path), str(second_path)])
    await expect(page.locator("#queue-count")).to_have_text("2")
    await expect(page.locator("#choose-button")).to_be_focused()
    await tab_to(
        page, '#file-list li:last-child button[data-action="remove"]', expect, stops
    )
    await page.keyboard.press("Enter")
    await expect(page.locator("#queue-count")).to_have_text("1")
    await expect(page.locator("#choose-button")).to_be_focused()
    await expect(page.locator("#file-list")).to_contain_text(first_path.name)
    await tab_to(page, "#convert-button", expect, stops)
    async with page.expect_response(
        lambda r: urlsplit(r.url).path == "/api/jobs" and r.request.method == "POST"
    ) as event:
        await page.keyboard.press("Enter")
    response = await event.value
    require(response.status == 202, "Keyboard upload was not accepted")
    payload = await response.json()
    require(
        len(payload["jobs"]) == 1 and not payload["errors"], "Keyboard queue mismatch"
    )
    job_id = payload["jobs"][0]["id"]
    await expect(page.locator("#queue-count")).to_have_text("0")
    await expect(page.locator("#download-button")).to_be_enabled(timeout=90000)
    select = f'#history-list button[data-action="select"][data-id="{job_id}"]'
    await tab_to(page, select, expect, stops)
    await page.keyboard.press("Enter")
    await expect(page.locator(select)).to_be_focused()
    await expect(page.locator("#markdown-preview")).to_contain_text(FIRST_MARKER)
    await tab_to(page, "#preview-tab", expect, stops)
    await page.keyboard.press("ArrowRight")
    await expect(page.locator("#source-tab")).to_be_focused()
    await expect(page.locator("#source-tab")).to_have_attribute("aria-selected", "true")
    await tab_to(page, "#markdown-source", expect, stops)
    result = await api_ok(page, f"/api/jobs/{job_id}")
    await expect(page.locator("#markdown-source")).to_have_value(result["markdown"])
    await page.keyboard.press("Control+Home")
    await tab_to(page, "#download-button", expect, stops)
    md = await save_download(
        page,
        "#download-button",
        "downloads/keyboard-result.md",
        evidence,
        keyboard=True,
    )
    require(
        md.read_text(encoding="utf-8") == result["markdown"], "Keyboard MD bytes differ"
    )
    checkbox = f'#history-list input[data-action="archive"][data-id="{job_id}"]'
    await tab_to(page, checkbox, expect, stops)
    await page.keyboard.press("Space")
    await expect(page.locator(checkbox)).to_be_checked()
    await expect(page.locator(checkbox)).to_be_focused()
    await tab_to(page, "#archive-button", expect, stops)
    archive_file = await save_download(
        page,
        "#archive-button",
        "downloads/keyboard-results.zip",
        evidence,
        keyboard=True,
    )
    with zipfile.ZipFile(archive_file) as archive:
        names = archive.namelist()
        require(
            len(names) == 1 and names[0].endswith(".md"), "Keyboard ZIP contents differ"
        )
        require(
            not Path(names[0]).is_absolute() and ".." not in Path(names[0]).parts,
            "Unsafe keyboard ZIP member",
        )
        require(
            archive.read(names[0]).decode("utf-8") == result["markdown"],
            "Keyboard ZIP Markdown differs",
        )
    require(
        (await api_ok(page, "/api/me"))["usage"]["used"] == before["usage"]["used"] + 1,
        "Keyboard flow charged an unexpected quota",
    )
    await evidence.screenshot(page, "keyboard-export")
    return {
        "focus_stops": stops,
        "input_method": "Tab/Enter/arrow/Space and keyboard text events",
        "file_selection": "chooser triggered with Enter; fixture bytes injected with set_files; native dialog not accepted",
        "screen_reader": "not tested",
    }


async def exercise(
    browser, origin: str, scratch: Path, password: str, evidence, expect
):
    contexts = []

    async def new_context(mobile=False):
        context = await browser.new_context(
            base_url=origin,
            viewport={
                "width": 390 if mobile else 1440,
                "height": 844 if mobile else 1000,
            },
            locale="zh-CN",
            timezone_id="UTC",
            color_scheme="light",
            is_mobile=mobile,
            has_touch=mobile,
            accept_downloads=True,
            service_workers="block",
        )
        contexts.append(context)

        async def keep_local(route):
            if urlsplit(route.request.url).netloc == urlsplit(origin).netloc:
                await route.continue_()
            else:
                evidence.data["unexpected_external_requests"].append(route.request.url)
                await route.abort("blockedbyclient")

        await context.route("**/*", keep_local)
        context.on(
            "page",
            lambda page: page.on(
                "pageerror",
                lambda error: evidence.data["page_errors"].append(str(error)),
            ),
        )
        context.set_default_timeout(15000)
        return context

    admin_context = await new_context()
    user_context = await new_context()
    other_context = await new_context()
    admin = await admin_context.new_page()
    user = await user_context.new_page()
    other = await other_context.new_page()
    first_path, second_path = write_fixtures(scratch)
    keyboard_page = None
    try:
        with evidence.step(STEPS[1]):
            await admin.goto("/app")
            await expect(admin.locator("#login-button")).to_be_enabled()
            await admin.keyboard.press("Tab")
            await expect(admin.locator(".skip-link")).to_be_focused()
            await admin.keyboard.press("Enter")
            await expect(admin.locator("#main-content")).to_be_focused()
            await tab_to(admin, "#login-tab", expect, [])
            await admin.keyboard.press("End")
            await expect(admin.locator("#register-tab")).to_be_focused()
            await admin.keyboard.press("Home")
            await expect(admin.locator("#login-tab")).to_be_focused()
            await evidence.screenshot(admin, "desktop-login")
            await login(admin, "synthetic-admin", password, expect)
            await open_admin(admin, expect)

        with evidence.step(STEPS[2]) as check:
            token, invite = await invitation(admin, expect, repeated=True)
            await user.goto("/app")
            await register(user, "synthetic-reader", token, expect)
            reused = await api(
                user,
                "/api/auth/register",
                "POST",
                {
                    "username": "synthetic-reuse",
                    "password": USER_PASSWORD,
                    "invite_token": token,
                },
            )
            require(reused["status"] == 400, "A used invitation was accepted again")
            for ttl in [0, 169]:
                rejected = await api(
                    admin, "/api/admin/invites", "POST", {"ttl_hours": ttl}
                )
                require(
                    rejected["status"] == 422, "Out-of-bounds invitation TTL accepted"
                )
            token2, _ = await invitation(admin, expect, hours=168)
            await other.goto("/app")
            await register(other, "synthetic-other", token2, expect)
            check["details"] = {
                "password_characters": len(USER_PASSWORD),
                "ttl_hours": [1, 168],
                "rejected_ttl_hours": [0, 169],
            }

        with evidence.step(STEPS[3]) as check:
            uploads = []

            def record_upload(request):
                if (
                    urlsplit(request.url).path == "/api/jobs"
                    and request.method == "POST"
                ):
                    uploads.append(request.url)

            user.on("request", record_upload)
            first = await upload(user, first_path, expect, repeated=True)
            user.remove_listener("request", record_upload)
            require(len(uploads) == 1, "Double click created multiple HTTP submissions")
            require(
                len((await api_ok(user, "/api/jobs"))["jobs"]) == 1,
                "Duplicate job created",
            )
            await expect(user.locator("#markdown-preview")).to_contain_text(
                FIRST_MARKER
            )
            await expect(user.locator("#output-label")).to_have_attribute(
                "data-state", "succeeded"
            )
            require(
                await user.evaluate("window.syntheticDocumentExecuted !== true"),
                "Document script executed",
            )
            require(
                await user.locator(
                    "#markdown-preview script, #markdown-preview img, "
                    "#markdown-preview a[href], #markdown-preview iframe, "
                    "#markdown-preview object, #markdown-preview embed"
                ).count()
                == 0,
                "Unsafe active preview content",
            )
            # The sanitizer preserves inert <a> text but removes every attribute,
            # including href. Reject any attribute rather than harmless anchors.
            require(
                await user.locator("#markdown-preview *").evaluate_all(
                    "nodes => nodes.every(node => node.getAttributeNames().length === 0)"
                ),
                "Preview retained an attribute",
            )
            await evidence.screenshot(user, "desktop-preview")
            check["details"] = {
                "http_submissions": len(uploads),
                "source_sha256": first["source_sha256"],
            }

        with evidence.step(STEPS[4]):
            await user.locator("#preview-tab").focus()
            await user.keyboard.press("ArrowRight")
            await expect(user.locator("#source-tab")).to_be_focused()
            await expect(user.locator("#markdown-source")).to_have_value(
                first["markdown"]
            )
            await user.keyboard.press("End")
            await expect(user.locator("#split-tab")).to_be_focused()
            await expect(user.locator("#preview-panel")).to_be_visible()
            await expect(user.locator("#source-panel")).to_be_visible()
            await evidence.screenshot(user, "desktop-compare")
            # Deliberate browser capability failure, not a mock app/API/result.
            await user.evaluate(
                """() => {
                Object.defineProperty(navigator, 'clipboard', {configurable: true,
                    value: {writeText: async () => {throw new DOMException('Synthetic denial', 'NotAllowedError');}}});
                document.execCommand = () => false;
            }"""
            )
            await user.locator("#copy-button").click()
            await expect(user.locator("#notice")).to_contain_text("系统复制快捷键")
            await expect(user.locator("#markdown-source")).to_be_focused()
            require(
                await user.locator("#markdown-source").evaluate(
                    "el => el.selectionStart === 0 && el.selectionEnd === el.value.length"
                ),
                "Clipboard failure did not select all source",
            )
            await expect(user.locator("#source-tab")).to_have_attribute(
                "aria-selected", "true"
            )
            await evidence.screenshot(user, "desktop-copy-fallback")

        with evidence.step(STEPS[5]):
            md = await save_download(
                user, "#download-button", "downloads/result.md", evidence
            )
            require(
                md.read_text(encoding="utf-8") == first["markdown"],
                "MD bytes differ from authenticated result",
            )
            await user.locator("#job-facts summary").click()
            manifest_file = await save_download(
                user, "#manifest-download", "downloads/manifest.json", evidence
            )
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            require(manifest["job_id"] == first["id"], "Wrong manifest job")
            require(
                manifest["source_sha256"]
                == hashlib.sha256(first_path.read_bytes()).hexdigest(),
                "Manifest source hash differs",
            )
            require(
                manifest["native_document_json"] is False
                and manifest["ocr_enabled"] is False,
                "Manifest misstates capabilities",
            )
            require(
                bool(manifest["attempt_history"])
                and bool(manifest["submission_snapshot"]),
                "Missing attempt/config facts",
            )
            await user.locator(
                f'input[data-action="archive"][data-id="{first["id"]}"]'
            ).check()
            archive_file = await save_download(
                user, "#archive-button", "downloads/results.zip", evidence
            )
            with zipfile.ZipFile(archive_file) as archive:
                names = archive.namelist()
                require(
                    len(names) == 1 and names[0].endswith(".md"),
                    "Unexpected ZIP contents",
                )
                require(
                    not Path(names[0]).is_absolute()
                    and ".." not in Path(names[0]).parts,
                    "Unsafe ZIP entry",
                )
                require(
                    archive.read(names[0]).decode("utf-8") == first["markdown"],
                    "ZIP Markdown differs",
                )

        with evidence.step(STEPS[6]) as check:
            denied = []
            for page, role in [(other, "other_member"), (admin, "administrator")]:
                for suffix in ["", "/download", "/manifest"]:
                    path = f"/api/jobs/{first['id']}{suffix}"
                    require(
                        (await api(page, path))["status"] == 404,
                        f"{role} read another owner's artifact",
                    )
                    denied.append(f"{role}: GET {suffix or 'detail'}")
                for method, suffix in [
                    ("POST", "/retry"),
                    ("POST", "/cancel"),
                    ("DELETE", ""),
                ]:
                    require(
                        (await api(page, f"/api/jobs/{first['id']}{suffix}", method))[
                            "status"
                        ]
                        == 404,
                        f"{role} mutated another owner's job",
                    )
                require(
                    (
                        await api(
                            page,
                            "/api/jobs/archive",
                            "POST",
                            {"job_ids": [first["id"]]},
                        )
                    )["status"]
                    == 404,
                    f"{role} archived another owner's artifact",
                )
                require(
                    not (await api_ok(page, "/api/jobs"))["jobs"],
                    f"{role} history leaked another owner",
                )
            for path in [
                "/api/admin/settings",
                "/api/admin/users",
                "/api/admin/invites",
                "/api/admin/audit",
            ]:
                require(
                    (await api(user, path))["status"] == 403,
                    f"Member read admin route {path}",
                )
            await expect(user.locator("#admin-toggle")).to_be_hidden()
            check["details"] = {
                "read_denials": denied,
                "mutations_and_archive_denied": True,
            }

        with evidence.step(STEPS[7]):
            second = await upload(user, second_path, expect)
            await user.reload()
            await expect(user.locator("#account-name")).to_have_text("synthetic-reader")
            await expect(user.locator("#history-count")).to_have_text("2")
            select_first = f'button[data-action="select"][data-id="{first["id"]}"]'
            select_second = f'button[data-action="select"][data-id="{second["id"]}"]'
            async with DelayedDetail(user, origin, first["id"], evidence) as delayed:
                await user.locator(select_first).click()
                await delayed.wait()
                await user.locator(select_second).click()
                await expect(user.locator("#markdown-preview")).to_contain_text(
                    SECOND_MARKER
                )
            await expect(user.locator("#markdown-preview")).to_contain_text(
                SECOND_MARKER
            )
            await expect(user.locator("#document-name")).to_have_text(second_path.name)
            await user.goto("/api/health")
            await user.go_back()
            await expect(user.locator("#account-name")).to_have_text("synthetic-reader")
            await expect(user.locator("#history-count")).to_have_text("2")
            await user.locator(select_first).click()
            await expect(user.locator("#download-button")).to_be_enabled()

        with evidence.step(STEPS[8]) as check:
            current = await api_ok(admin, "/api/admin/settings")
            stale_admin = await admin_context.new_page()
            await stale_admin.goto("/app")
            await expect(stale_admin.locator("#session-section")).to_be_visible()
            await open_admin(stale_admin, expect)
            await stale_admin.locator("#setting-daily-quota").fill("73")
            await expect(admin.locator("#setting-daily-quota")).to_have_value(
                str(current["current"]["default_daily_quota"])
            )
            await expect(admin.locator("#setting-file-mib")).to_have_value(
                str(current["current"]["default_max_file_bytes"] // 1048576)
            )
            await expect(admin.locator("#setting-retention-hours")).to_have_value(
                str(current["current"]["retention_seconds"] // 3600)
            )
            require(
                await admin.locator(
                    "#service-config input, #service-config button"
                ).count()
                == 0,
                "Deployment controls are editable",
            )
            await admin.locator("#setting-daily-quota").fill("61")
            await admin.locator("#setting-file-mib").fill("9")
            await admin.locator("#setting-retention-hours").fill("2")
            await confirm_click(admin, "#settings-save", ("配置版本", "新账户"), accept=False)
            require(
                (await api_ok(admin, "/api/admin/settings"))["version"]
                == current["version"],
                "Dismissed confirmation changed settings",
            )
            await confirm_click(
                admin, "#settings-save", ("配置版本", "新账户", "已有账户和任务到期时间不变")
            )
            await expect(admin.locator("#settings-status")).to_contain_text("已保存配置版本")
            saved = await api_ok(admin, "/api/admin/settings")
            require(
                saved["version"] == current["version"] + 1,
                "Settings version did not increment",
            )
            async with stale_admin.expect_response(
                lambda r: urlsplit(r.url).path == "/api/admin/settings"
                and r.request.method == "PATCH"
            ) as event:
                await confirm_click(stale_admin, "#settings-save", ("配置版本", "新账户"))
            require(
                (await event.value).status == 409, "Stale admin write was not rejected"
            )
            await expect(stale_admin.locator("#settings-status")).to_contain_text(
                "本次未保存"
            )
            await expect(stale_admin.locator("#setting-daily-quota")).to_have_value(
                "61"
            )
            await expect(stale_admin.locator("#setting-retention-hours")).to_have_value(
                "2"
            )
            old = await api_ok(user, f"/api/jobs/{first['id']}")
            require(
                old["expires_at"] == first["expires_at"],
                "Settings rewrote an old expiry",
            )
            require(
                (await api_ok(user, "/api/me"))["user"]["daily_quota"] == 50,
                "Defaults rewrote an existing account",
            )
            third_path = scratch / "合成-新保留期.txt"
            third_path.write_text("SYNTHETIC_NEW_RETENTION\n", encoding="utf-8")
            third = await upload(user, third_path, expect)
            require(
                abs(third["expires_at"] - third["created_at"] - 7200) < 1,
                "New job did not use new TTL",
            )
            token3, _ = await invitation(admin, expect)
            fresh_context = await new_context()
            fresh = await fresh_context.new_page()
            await fresh.goto("/app")
            await register(fresh, "synthetic-new-defaults", token3, expect)
            new_user = (await api_ok(fresh, "/api/me"))["user"]
            require(
                new_user["daily_quota"] == 61
                and new_user["max_file_bytes"] == 9 * 1048576,
                "New account did not inherit saved defaults",
            )
            await evidence.screenshot(admin, "desktop-admin-current-values")
            check["details"] = {
                "version_before": current["version"],
                "version_after": saved["version"],
                "new_job_ttl_seconds": 7200,
                "old_job_expiry_unchanged": True,
            }

        with evidence.step(STEPS[9]):
            await admin.locator("#admin-refresh").click()
            await expect(admin.locator("#admin-refresh")).to_be_enabled()
            admin_user = (await api_ok(admin, "/api/me"))["user"]
            self_form = admin.locator(f'form[data-user-id="{admin_user["id"]}"]')
            await expect(self_form.locator('[data-field="is_active"]')).to_be_disabled()
            self_disable = await api(
                admin,
                f"/api/admin/users/{admin_user['id']}",
                "PATCH",
                {"is_active": False},
            )
            require(self_disable["status"] == 400, "Admin self-disable was accepted")
            reader = (await api_ok(user, "/api/me"))["user"]
            reader_selector = f'form[data-user-id="{reader["id"]}"]'
            await expect(
                admin.locator(reader_selector).locator('[data-field="daily_quota"]')
            ).to_have_value("50")
            await admin.locator(reader_selector).locator(
                '[data-field="daily_quota"]'
            ).fill("37")
            await admin.locator(reader_selector).locator(
                '[data-field="max_file_mib"]'
            ).fill("5")
            await confirm_click(
                admin, reader_selector + " button", ("synthetic-reader", "已用额度不退")
            )
            await expect(admin.locator(reader_selector + " button")).to_be_enabled()
            await expect(
                admin.locator(reader_selector).locator('[data-field="daily_quota"]')
            ).to_have_value("37")
            updated_reader = (await api_ok(user, "/api/me"))["user"]
            require(
                updated_reader["daily_quota"] == 37
                and updated_reader["max_file_bytes"] == 5 * 1048576,
                "Account values were not saved",
            )
            other_user = (await api_ok(other, "/api/me"))["user"]
            other_selector = f'form[data-user-id="{other_user["id"]}"]'
            await admin.locator(other_selector).locator(
                '[data-field="is_active"]'
            ).uncheck()
            await confirm_click(
                admin, other_selector + " button", ("synthetic-other", "停用将撤销会话")
            )
            await expect(admin.locator(other_selector + " button")).to_be_enabled()
            await expect(admin.locator("#admin-refresh")).to_be_enabled()
            require(
                (await api(other, "/api/me"))["status"] == 401,
                "Disabled account session still works",
            )
            await other.locator("#refresh-button").click()
            await assert_private_cleared(other, expect)
            await admin.locator("#audit-refresh").click()
            await expect(admin.locator("#audit-refresh")).to_be_enabled()
            await expect(admin.locator("#audit-list")).to_contain_text("更新业务设置")
            await expect(admin.locator("#audit-list")).to_contain_text("更新账户")
            events = (await api_ok(admin, "/api/admin/audit"))["events"]
            event_text = json.dumps(events)
            for forbidden in [
                password,
                USER_PASSWORD,
                token,
                token2,
                token3,
                FIRST_MARKER,
                SECOND_MARKER,
            ]:
                require(
                    forbidden not in event_text,
                    "Audit contains a credential/token/document marker",
                )
            require(
                any(item["action"] == "settings.update" for item in events),
                "Missing settings audit",
            )
            require(
                any(item["action"] == "user.update" for item in events),
                "Missing account audit",
            )

        with evidence.step(STEPS[10]):
            async with DelayedDetail(user, origin, first["id"], evidence) as delayed:
                await user.locator(select_first).click()
                await delayed.wait()
                await user.locator("#logout-button").click()
                await assert_private_cleared(user, expect)
                await expect(user.locator("#auth-message")).to_contain_text("已退出登录")
            await assert_private_cleared(user, expect)
            require(
                (await api(user, "/api/me"))["status"] == 401,
                "Logout did not revoke server session",
            )
            require(
                (await api(user, f"/api/jobs/{first['id']}/download"))["status"] == 401,
                "Logged-out download succeeded",
            )
            await user.goto("/api/health")
            await user.go_back()
            await expect(user.locator("#login-button")).to_be_enabled()
            await assert_private_cleared(user, expect)
            await user.reload()
            await expect(user.locator("#login-button")).to_be_enabled()
            await assert_private_cleared(user, expect)
            await evidence.screenshot(user, "desktop-logged-out")

        with evidence.step(STEPS[11]):
            mobile_context = await new_context(mobile=True)
            mobile = await mobile_context.new_page()
            await mobile.goto("/app")
            await expect(mobile.locator("#login-button")).to_be_enabled()
            await evidence.screenshot(mobile, "mobile-390-login")
            await login(mobile, "synthetic-reader", USER_PASSWORD, expect)
            await expect(mobile.locator("#history-count")).to_have_text("3")
            await mobile.locator(select_first).click()
            await expect(mobile.locator("#download-button")).to_be_enabled()
            await mobile.locator("#preview-tab").focus()
            await mobile.keyboard.press("End")
            await expect(mobile.locator("#split-tab")).to_be_focused()
            await expect(mobile.locator("#source-panel")).to_be_visible()
            await evidence.screenshot(mobile, "mobile-390-compare")
            await mobile.set_viewport_size({"width": 320, "height": 740})
            await evidence.screenshot(mobile, "mobile-320-compare")
            await mobile.locator("#logout-button").click()
            await expect(mobile.locator("#auth-message")).to_contain_text("已退出登录")
            await login(mobile, "synthetic-admin", password, expect)
            await open_admin(mobile, expect)
            await evidence.screenshot(mobile, "mobile-320-admin")
            await mobile.set_viewport_size({"width": 390, "height": 844})
            await evidence.screenshot(mobile, "mobile-390-admin")
            require(
                not evidence.data["page_errors"], "Uncaught browser JavaScript error"
            )
            require(
                not evidence.data["unexpected_external_requests"],
                "Browser attempted external document network access",
            )
        with evidence.step(STEPS[12]) as check:
            keyboard_context = await new_context()
            keyboard_page = await keyboard_context.new_page()
            check["details"] = await keyboard_journey(
                keyboard_page, first_path, second_path, evidence, expect
            )
            require(
                not evidence.data["page_errors"], "Uncaught browser JavaScript error"
            )
            require(
                not evidence.data["unexpected_external_requests"],
                "Unexpected external request",
            )
    except BaseException:
        # Preserve actual failure pixels, not a fabricated mock or DOM rendering.
        for label, page in [
            ("failure-admin", admin),
            ("failure-reader", user),
            ("failure-keyboard", keyboard_page),
        ]:
            try:
                if page is not None and not page.is_closed():
                    await evidence.screenshot(page, label)
            except Exception as capture_error:
                evidence.data.setdefault("capture_errors", []).append(
                    str(capture_error)
                )
        raise
    finally:
        for context in reversed(contexts):
            await context.close()


async def run(output: Path) -> int:
    evidence = Evidence(output)
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    password = secrets.token_urlsafe(32)
    try:
        with tempfile.TemporaryDirectory(prefix="markitdown-browser-") as temp:
            scratch = Path(temp)
            async with local_server(scratch / "private-data", password) as origin:
                with evidence.step(STEPS[0]):
                    from playwright.async_api import async_playwright, expect

                    installed = importlib.metadata.version("playwright")
                    require(
                        installed == PLAYWRIGHT_VERSION,
                        "Install the exact requirements-browser-test.in Playwright pin",
                    )
                    playwright = await async_playwright().start()
                    try:
                        browser = await playwright.chromium.launch(
                            headless=True,
                            chromium_sandbox=True,
                            downloads_path=str(scratch / "browser-downloads"),
                        )
                    except BaseException:
                        await playwright.stop()
                        raise
                    evidence.data["browser"] = {
                        "engine": "chromium",
                        "version": browser.version,
                        "playwright_version": installed,
                        "chromium_sandbox": True,
                        "headless": True,
                        "custom_launch_args": [],
                    }
                try:
                    await exercise(browser, origin, scratch, password, evidence, expect)
                finally:
                    await browser.close()
                    await playwright.stop()
        evidence.data["automated_status"] = "passed"
        return 0
    except BaseException as error:
        evidence.data["automated_status"] = "failed"
        evidence.data["failure"] = {"type": type(error).__name__, "message": str(error)}
        artifact_path(output, "failure.txt").write_text(
            traceback.format_exc(), encoding="utf-8"
        )
        print("FAILED " + json.dumps(public_failure(error)), file=sys.stderr)
        return 1
    finally:
        evidence.data["finished_at"] = now()
        evidence.flush()
        print(f"Evidence: {output / 'evidence.json'}", flush=True)
        print(
            "Human visual review remains required; screenshots are not visual acceptance."
        )


class HarnessSelfChecks(unittest.TestCase):
    """Pure stdlib checks. These do not count as any real-browser gate."""

    def test_six_character_synthetic_password(self):
        self.assertEqual(len(USER_PASSWORD), 6)

    def test_artifacts_cannot_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for unsafe in ["../escape", "/tmp/escape", "downloads/../../escape"]:
                with self.assertRaises(ValueError):
                    artifact_path(root, unsafe)
            self.assertEqual(
                artifact_path(root, "downloads/result.md"), root / "downloads/result.md"
            )

    def test_no_artifact_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "link").symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                artifact_path(root, "link/output.json")

    def test_preserve_previous_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "existing.json").write_text("preserve me", encoding="utf-8")
            with self.assertRaises(ValueError):
                prepare_output(root)
            self.assertEqual((root / "existing.json").read_text(), "preserve me")

    def test_real_fixture_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = write_fixtures(Path(directory))
            self.assertIn(FIRST_MARKER, first.read_text(encoding="utf-8"))
            self.assertIn(SECOND_MARKER, second.read_text(encoding="utf-8"))
            self.assertIn("中文", first.read_text(encoding="utf-8"))
            self.assertNotEqual(first.read_bytes(), second.read_bytes())

    def test_static_selector_ids_exist(self):
        source = Path(__file__).read_text(encoding="utf-8")
        html = (
            ROOT / "packages/markitdown-web/src/markitdown_web/static/index.html"
        ).read_text(encoding="utf-8")
        ids = set(re.findall(r'id="([a-z][a-z0-9-]+)"', html))
        referenced = set(re.findall(r"#[a-z][a-z0-9-]+", source))
        missing = {item[1:] for item in referenced} - ids
        self.assertFalse(missing, f"Unknown static IDs: {missing}")

    def test_pin_matches_requirements(self):
        requirements = (ROOT / "requirements-browser-test.in").read_text(
            encoding="utf-8"
        )
        self.assertIn(f"playwright=={PLAYWRIGHT_VERSION}", requirements)

    def test_keyboard_journey_has_no_pointer_or_programmatic_focus_shortcuts(self):
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        for function in tree.body:
            if isinstance(function, ast.AsyncFunctionDef) and function.name in {
                "keyboard_journey",
                "keyboard_choose",
                "tab_to",
            }:
                shortcuts = {
                    call.func.attr
                    for call in ast.walk(function)
                    if isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr
                    in {
                        "click",
                        "dblclick",
                        "fill",
                        "focus",
                        "check",
                        "dispatch_event",
                        "set_input_files",
                    }
                }
                self.assertFalse(
                    shortcuts, f"Keyboard journey has shortcuts: {shortcuts}"
                )

    def test_chooser_expectation_precedes_keyboard_traversal(self):
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        traversals = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "tab_to"
            and len(node.args) > 1
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "#choose-button"
        ]
        self.assertEqual(len(traversals), 1)
        armed = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncWith)
            and any(
                isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Attribute)
                and item.context_expr.func.attr == "expect_file_chooser"
                for item in node.items
            )
        ]
        self.assertTrue(
            any(traversals[0] in list(ast.walk(node)) for node in armed),
            "Chooser interception must be armed before Tab traversal",
        )
        for block in armed:
            enters = [
                node
                for node in ast.walk(block)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "press"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "Enter"
            ]
            self.assertEqual(len(enters), 1, "The chooser gesture must use one Enter")

    def test_chooser_diagnostics_exclude_content_and_allow_only_bounded_facts(self):
        error = AssertionError("private-document-and-token")
        error.keyboard_chooser_diagnostics = {
            "focused": True,
            "enter_count": 1,
            "event_seen": False,
            "input_enabled": "private-password",
            "chooser_click_count": 1000,
            "username": "private-user",
            "source": "private-document",
        }
        result = public_failure(error)
        self.assertEqual(
            result["keyboard_chooser"],
            {"focused": True, "enter_count": 1, "event_seen": False},
        )
        self.assertNotIn("private-", json.dumps(result))

    def test_checks_are_unique(self):
        self.assertEqual(len(STEPS), len(set(STEPS)))

    def test_public_failure_omits_private_page_values(self):
        result = public_failure(AssertionError("private-document-and-token"))
        self.assertNotIn("private-document-and-token", json.dumps(result))
        self.assertEqual(result["category"], "assertion_failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, help="New/empty directory for evidence, PNGs and downloads"
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Stdlib-only harness checks; no server/browser/network",
    )
    arguments = parser.parse_args()
    if arguments.self_check:
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(HarnessSelfChecks)
        )
        return 0 if result.wasSuccessful() else 1
    if arguments.out is None:
        parser.error("--out is required for a real-browser run")
    os.umask(0o077)
    try:
        output = prepare_output(arguments.out)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return asyncio.run(run(output))


if __name__ == "__main__":
    raise SystemExit(main())
