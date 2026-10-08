"""Screenshot and verify the actual product site on an owned ephemeral service."""

import argparse
import asyncio
import hashlib
import importlib.util
import json
import secrets
import tempfile
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "browser_e2e", ROOT / "packages/markitdown-web/tests/browser_e2e.py"
)
assert spec and spec.loader
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


async def run(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    results: dict = {"kind": "synthetic_product_site", "viewports": [], "downloads": []}
    errors = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            with tempfile.TemporaryDirectory(prefix="markitdown-site-") as directory:
                async with harness.local_server(
                    Path(directory), secrets.token_urlsafe(32)
                ) as origin:
                    context = await browser.new_context(
                        base_url=origin, accept_downloads=True
                    )
                    page = await context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    for width in [1440, 390, 320]:
                        await page.set_viewport_size({"width": width, "height": 960})
                        await page.goto("/")
                        await page.wait_for_function(
                            "!document.getElementById('download-status').textContent.includes('正在读取')"
                        )
                        assert await page.locator(".product-shot img").evaluate(
                            "image => image.complete && image.naturalWidth > 0"
                        )
                        assert await page.evaluate(
                            "document.documentElement.scrollWidth <= innerWidth"
                        )
                        await page.screenshot(
                            path=str(output / f"site-{width}.png"), full_page=True
                        )
                        await page.locator(".faq-list summary").first.click()
                        assert (
                            await page.locator(".faq-list details").first.get_attribute(
                                "open"
                            )
                            is not None
                        )
                        await page.locator(".faq-list summary").first.click()
                        await page.locator(".nav-download").click()
                        assert await page.locator("#download").is_visible()
                        results["viewports"].append({"width": width, "overflow": False})
                    metadata = await (
                        await context.request.get("/api/downloads")
                    ).json()
                    assert await page.locator(".download-entry a").count() == len(
                        {entry["platform"] for entry in metadata["downloads"]}
                    )
                    for entry in metadata["downloads"]:
                        selector = f'.download-entry a[href="{entry["url"]}"]'
                        async with page.expect_download() as event:
                            await page.locator(selector).click()
                        download = await event.value
                        assert await download.failure() is None
                        with tempfile.TemporaryDirectory(
                            prefix="markitdown-install-download-"
                        ) as staging:
                            file = Path(staging) / entry["filename"]
                            await download.save_as(str(file))
                            with file.open("rb") as stream:
                                digest = hashlib.file_digest(
                                    stream, "sha256"
                                ).hexdigest()
                            assert digest == entry["sha256"]
                            assert file.stat().st_size == entry["bytes"]
                        results["downloads"].append(
                            {
                                "platform": entry["platform"],
                                "sha256": digest,
                                "passed": True,
                            }
                        )
                    await context.close()
        finally:
            await browser.close()
    assert not errors, errors
    results["page_errors"] = errors
    results["automated_status"] = "passed"
    results["visual_review"] = "pending"
    (output / "site-evidence.json").write_text(json.dumps(results, indent=2) + "\n")
    print(
        "Product site passed: desktop/390/320 layout, actual screenshot, FAQ/navigation, real installer size/SHA256."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    arguments = parser.parse_args()
    asyncio.run(run(arguments.out.resolve()))
