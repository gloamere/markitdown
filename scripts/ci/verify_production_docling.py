"""Bounded real Docling/preview smoke in the existing production runtime.

Requires an already-provisioned exact image and the existing five pinned model
files. No downloading, local-mode fallback, mocks or host-policy changes occur
here. Synthetic inputs and returned document text stay in private temporary
storage; only fixed check names, booleans, hashes and metadata are reported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from pathlib import Path

from markitdown_web import engines, sandbox
from markitdown_web.conversion import MAX_HTML_BYTES, SAFE_ERRORS, ConversionError
from markitdown_web.docling_adapter import ASSETS, MAX_MARKDOWN_BYTES, PROFILE, VERSION
from markitdown_web.preview import validate_html
from markitdown_web.state import Settings

MANIFEST = Path(__file__).resolve().parents[1] / "docling/models.json"
CHECKS = (
    "production_runtime_paths",
    "model_manifest_contract",
    "live_probe_and_model_checksums",
    "two_page_preflight",
    "two_page_conversion_and_independent_preview",
    "three_page_rejected",
    "private_workspaces_cleaned",
)
# Existing supervisor refusals are fixed strings in engines.py. Map only exact
# matches; never include an unrecognized exception message in the report.
SUPERVISOR_ERRORS = {
    "resource_monitoring_unavailable": "运行资源监测不可用，任务已停止；请联系管理员",
    "resource_limit": "PDF 增强超出资源限制，请拆分文档后重试",
    "insufficient_temporary_storage": "临时存储空间不足，请稍后重试",
    "sandbox_unavailable": sandbox.UNAVAILABLE,
    "preflight_busy": "PDF 页数校验繁忙，请稍后重试",
    "deadline_exceeded": "PDF 增强超时，请拆分文档后重试",
    "preflight_timeout": f"PDF 增强超时（{engines.PREFLIGHT_SECONDS} 秒），请拆分文档后重试",
    "conversion_timeout": f"PDF 增强超时（{engines.WALL_SECONDS} 秒），请拆分文档后重试",
}


class ThreePageAccepted(AssertionError):
    """The real admission API returned successfully for the three-page input."""


def synthetic_pdf(pages: int) -> bytes:
    """Extend the existing stdlib PDF fixture shape to exactly two or three pages."""
    if type(pages) is not int or pages not in (2, 3):
        raise ValueError("unsupported-synthetic-page-count")
    kids = " ".join(f"{number + 3} 0 R" for number in range(pages))
    font = 3 + pages * 2
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode("ascii"),
    ]
    for number in range(pages):
        content = 3 + pages + number
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font} 0 R >> >> "
                f"/Contents {content} 0 R >>"
            ).encode("ascii")
        )
    for number in range(1, pages + 1):
        stream = (
            "BT /F1 18 Tf 50 700 Td " f"(Synthetic production page {number}) Tj ET"
        ).encode("ascii")
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    data = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
    startxref = len(data)
    data.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    data.extend(
        (
            f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
            f"startxref\n{startxref}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(data)


def production_settings(data: Path, runtime: Path, models: Path) -> Settings:
    # Do not resolve supplied symlinks away before the production guards see them.
    return Settings(
        data_dir=data,
        deployment_mode="production",
        public_origin="https://synthetic.invalid",
        cookie_secure=True,
        sandbox_runtime_root=runtime,
        docling_enabled=True,
        docling_python=None,
        docling_models=models,
        start_workers=False,
    )


def model_manifest_metadata() -> dict:
    raw = MANIFEST.read_bytes()
    manifest = json.loads(raw)
    items = manifest["models"]
    pins = {item["local_path"]: (item["bytes"], item["sha256"]) for item in items}
    if (
        len(items) != 5
        or pins != ASSETS
        or sum(size for size, _ in ASSETS.values()) != manifest["total_model_bytes"]
        or any(not re.fullmatch(r"[a-f0-9]{40}", item["revision"]) for item in items)
    ):
        raise ValueError("model-manifest-contract")
    return {
        "model_files": len(items),
        "model_bytes": manifest["total_model_bytes"],
        "model_manifest_sha256": hashlib.sha256(raw).hexdigest(),
    }


def verify_runtime(settings: Settings, image_id: str) -> dict:
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise ValueError("invalid-image-identity")
    # Both interpreters must belong to the very same prepared image. The local
    # Docling interpreter setting cannot satisfy either production path.
    for engine, expected in (
        ("docling", "/opt/docling/bin/python"),
        ("preview", "/usr/bin/python3"),
    ):
        _, _, python = sandbox.runtime_paths(settings, engine)
        snapshot = sandbox.execution_snapshot(settings, engine)
        if (
            python != expected
            or snapshot["runtime_image_id"] != image_id
            or snapshot["sandbox_profile"] != sandbox.PROFILE
            or snapshot["production_boundary"] is not True
        ):
            raise ValueError("runtime-identity-mismatch")
    return {
        "runtime_image_id": image_id,
        "sandbox_profile": sandbox.PROFILE,
        "production_boundary": True,
        "rss_measurement_scope": "complete-wrapper-tree-required",
    }


def failure_metadata(exc: Exception) -> dict:
    """Keep diagnostics useful without trusting any exception message as output."""
    result = {"exception_type": type(exc).__name__}
    known = {
        message: code
        for messages in (engines.ERRORS, SAFE_ERRORS, SUPERVISOR_ERRORS)
        for code, message in messages.items()
    }
    code = known.get(str(exc))
    if code is not None:
        result["error_code"] = code
    return result


def require_three_page_rejection(settings: Settings, payload: bytes) -> None:
    """Accept only the canonical page-limit refusal; preserve other failures."""
    try:
        engines.validate_engine_uploads(
            "docling", [{"suffix": ".pdf", "data": payload}], settings
        )
    except ConversionError as exc:
        if str(exc) != engines.ERRORS["page_limit"]:
            raise
    else:
        raise ThreePageAccepted


def run_smoke(runtime: Path, models: Path, image_id: str) -> dict:
    report: dict = {
        "schema_version": 1,
        "success": False,
        "checks": {name: False for name in CHECKS},
        "limits": (
            "Bounded synthetic existing-path smoke only; not a corpus benchmark, "
            "target-host capacity, TLS, browser or document-quality acceptance"
        ),
    }
    stage = CHECKS[0]
    try:
        with tempfile.TemporaryDirectory(
            prefix="markitdown-production-docling-"
        ) as name:
            root = Path(name).resolve()
            settings = production_settings(root, runtime, models)
            report.update(verify_runtime(settings, image_id))
            report["checks"][stage] = True

            stage = CHECKS[1]
            report.update(model_manifest_metadata())
            report["checks"][stage] = True

            stage = CHECKS[2]
            # This is a dedicated one-shot process; require the first API call
            # to take the real probe path rather than an earlier cached result.
            if engines._probe_cache:
                raise RuntimeError("smoke-requires-fresh-process")
            engines.ensure_engine_available("docling", settings)
            report["checks"][stage] = True

            two_pages = synthetic_pdf(2)
            stage = CHECKS[3]
            engines.validate_engine_uploads(
                "docling", [{"suffix": ".pdf", "data": two_pages}], settings
            )
            report["checks"][stage] = True

            stage = CHECKS[4]
            source = root / "two-pages.pdf"
            source.touch(mode=0o600, exist_ok=False)
            source.write_bytes(two_pages)
            # The existing API rebuilds preview in its own restricted worker.
            # Do not bypass it with an in-process renderer or trust worker HTML.
            markdown, html, metadata = engines.run_docling_conversion(
                source, settings, lambda: False
            )
            expected = [f"Synthetic production page {number}" for number in (1, 2)]
            if (
                not all(fact in markdown and fact in html for fact in expected)
                or not 0 < len(markdown.encode("utf-8")) <= MAX_MARKDOWN_BYTES
                or not 0 < len(html.encode("utf-8")) <= MAX_HTML_BYTES
                or not validate_html(html)
                or metadata["page_count"] != 2
                or metadata["engine"] != "docling"
                or metadata["version"] != VERSION
                or metadata["profile"] != PROFILE
                or metadata["ocr"] is not False
                or source.read_bytes() != two_pages
            ):
                raise ValueError("two-page-result-contract")
            report["two_page"] = {
                "source_sha256": hashlib.sha256(two_pages).hexdigest(),
                "markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
                "preview_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
                "both_page_facts_present": True,
                "source_unchanged": True,
                # Select known metadata, never copy document-controlled fields.
                "engine": metadata["engine"],
                "version": metadata["version"],
                "profile": metadata["profile"],
                "ocr": metadata["ocr"],
                "page_count": metadata["page_count"],
                "duration_seconds": metadata["duration_seconds"],
            }
            report["checks"][stage] = True

            stage = CHECKS[5]
            three_pages = synthetic_pdf(3)
            require_three_page_rejection(settings, three_pages)
            report["three_page"] = {
                "source_sha256": hashlib.sha256(three_pages).hexdigest(),
                "rejection_code": "page_limit",
                "rejected": True,
            }
            report["checks"][stage] = True

            stage = CHECKS[6]
            if (
                any(root.glob("engine-*"))
                or any(root.glob("docling-*"))
                or any(root.glob("preview-*"))
                or sandbox.active_workspaces()
            ):
                raise ValueError("private-workspace-remains")
        if root.exists():
            raise ValueError("synthetic-workspace-remains")
        report["checks"][stage] = True
        report["success"] = True
    except Exception as exc:
        # Never publish exception messages, tracebacks, text or host paths.
        report["failed_check"] = stage
        report["failure"] = failure_metadata(exc)
    report["passed"] = sum(report["checks"].values())
    report["total"] = len(CHECKS)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run_smoke(args.runtime_root, args.models, args.image_id)
    try:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        report["success"] = False
        report["report_write_failed"] = True
        report["report_write_failure"] = failure_metadata(exc)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
