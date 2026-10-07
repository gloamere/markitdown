"""Own an isolated local service for native Electron and website acceptance."""

import argparse
import asyncio
import importlib.util
import os
import secrets
import tempfile
from pathlib import Path

from markitdown_web.auth import AuthService
from markitdown_web.state import Database, Settings

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "browser_e2e", ROOT / "packages/markitdown-web/tests/browser_e2e.py"
)
assert spec and spec.loader
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


async def run(output: Path, app: Path | None = None) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="markitdown-desktop-api-") as directory:
        data = Path(directory)
        async with harness.local_server(data, secrets.token_urlsafe(32)) as origin:
            settings = Settings(data_dir=data, start_workers=False)
            database = Database(settings)
            auth = AuthService(database, settings)
            with database.connect() as connection:
                admin_id = connection.execute(
                    "SELECT id FROM users WHERE username = 'synthetic-admin'"
                ).fetchone()["id"]
            invitation = auth.create_invite(admin_id)
            password = secrets.token_urlsafe(32)
            auth.register("demo-reader", password, invitation["token"], "127.0.0.1")
            environment = {
                **os.environ,
                "MARKITDOWN_SYNTHETIC_ORIGIN": origin,
                "MARKITDOWN_SYNTHETIC_PASSWORD": password,
                "MARKITDOWN_TEST_EVIDENCE": str(output),
            }
            if app:
                executable = app / "Contents/MacOS/MarkItDown"
                if not executable.is_file():
                    raise ValueError("Expected an actual macOS MarkItDown.app bundle")
                environment["MARKITDOWN_TEST_EXECUTABLE"] = str(executable)
            process = await asyncio.create_subprocess_exec(
                "node",
                str(ROOT / "packages/markitdown-desktop/tests/service-smoke.cjs"),
                cwd=ROOT,
                env=environment,
            )
            try:
                async with asyncio.timeout(180):
                    code = await process.wait()
            except TimeoutError:
                process.kill()
                await process.wait()
                raise
            if code:
                raise RuntimeError("Native desktop acceptance failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--app", type=Path, help="Test the actual macOS app bundle")
    arguments = parser.parse_args()
    asyncio.run(
        run(arguments.out.resolve(), arguments.app.resolve() if arguments.app else None)
    )
