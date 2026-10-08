#!/usr/bin/env python3
"""Small synthetic task-quality check; not a real-user study or accuracy benchmark."""
from __future__ import annotations

# ruff: noqa: BLE001 -- every synthetic failure remains in the denominator
import os

os.environ["ORT_DISABLE_TELEMETRY"] = "1"

import argparse
import hashlib
import io
import json
import platform
import re
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from markitdown_web.conversion import SAFE_ERRORS, ConversionError, run_conversion
from markitdown_web.state import Settings
from openpyxl import Workbook

REPO = Path(__file__).resolve().parents[1]
CORPUS_VERSION = "synthetic-export-v1"
FIXED_ZIP_TIME = (2000, 1, 1, 0, 0, 0)
FIXED_CORE_TIME = b"2000-01-01T00:00:00Z"
# This is an input contract, not a golden-output or quality score. Intentional
# source changes require a reviewed corpus-version/expectation update together.
INPUT_SHA256: dict[str, str] = {
    "format-txt": "9c1dee87558ea84abf23e3078b399b337fcb72b3136ada0a99b7566a2166acfd",
    "format-csv": "184cf8043c4ccac29cbc491b10e336bc10c910737876ce4f71f41e42d0279936",
    "format-json": "d496aaff68bdfc98608324e8e6368cfb3c2b268165de1e2920cfc5ff34b7448e",
    "format-md": "6aedf2890dca6d93481d5692a913836f8a458e86139d491df24e67544383764a",
    "format-docx": "9e85de25843934c0174c4cc3210166c4b9f7d9c194e54c843094d90a2055466e",
    "format-xlsx": "1334eddd4933bfad48f6956bb32a0487c40c39207c966b10205f4ce1f2422bc1",
    "format-pdf": "ff47292a3ba4e7b4a7c7f3387a9f69666e3a943d1e6dc45008f118299c500656",
    "structure-unicode-code": "498d5e21d9109889eeffcd7f5a08e6ba0dc770344131055e85ff765cda062dce",
    "empty-pdf": "3cdc644b9905639fd800496db74f85bfc197d2d98d08cd04518010870944bf3f",
    "malformed-pdf": "135c87d08d62e5a32ade684553b98c85e48f1662b7356d6091204d0d381adace",
    "malformed-docx": "6c4200e50c50a5370875c2859085e7ffb80b487fe2331c2769b1514971ac7b9b",
    "duplicate-name": "7b98b61db566e119395cb8e15863ba4cbbc988a1631013942f11119fbe29ba0e",
    "truncated-name-a": "c36fc19ddfa4923a57a675488716e03eca72fe3c98355dc31ab0b7cb1ce27190",
    "truncated-name-b": "57c7ca13ac1522c0674b6a30754a4b9e7559f317afbcc4330f096354bb000189",
}


@dataclass(frozen=True)
class Case:
    case_id: str
    filename: str
    data: bytes
    required_facts: tuple[str, ...] = ()
    expected_outcome: str = "success"
    expected_error_code: str | None = None
    exact_markdown: bool = False


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pdf_bytes(text: str = "Revenue 1234.50 CNY. Total 9 units.") -> bytes:
    text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 16 Tf 50 750 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, item in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(str(index).encode() + b" 0 obj\n" + item + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(data)


def canonical_office(data: bytes) -> bytes:
    """Normalize only our generated packages, never documents submitted by users.

    openpyxl rewrites modified time on save even if it was assigned beforehand.
    Normalize after save, including ZIP file-mtime/clock fields and core dates.
    Stored entries avoid compressor-version differences in the input contract.
    """
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(
        output, "w"
    ) as target:
        for name in sorted(source.namelist()):
            content = source.read(name)
            if name == "docProps/core.xml":
                content, count = re.subn(
                    rb"(<dcterms:(created|modified)\b[^>]*>)[^<]*(</dcterms:\2>)",
                    lambda match: match[1] + FIXED_CORE_TIME + match[3],
                    content,
                )
                if count != 2:
                    raise ValueError("Generated Office core dates changed structure")
            entry = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
            entry.create_system = 3
            entry.external_attr = 0o600 << 16
            entry.compress_type = zipfile.ZIP_STORED
            target.writestr(entry, content)
    return output.getvalue()


def fixtures() -> list[tuple[str, bytes, list[str]]]:
    """Compatibility helper: the original seven formats and required facts."""
    docx = io.BytesIO()
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>中文收入 1234.50 万元；时期 2026。</w:t></w:r></w:p></w:body></w:document>',
        )
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "合成表格"
    sheet.append(["品类", "金额（万元）", "数量"])
    sheet.append(["茶", 1234.5, 3])
    sheet.append(["咖啡", 80.25, 6])
    xlsx = io.BytesIO()
    workbook.save(xlsx)
    workbook.close()
    md = "# 合成离线笔记\n\n金额单位：万元，时期：2026。\n\n| 品类 | 金额 | 备注 |\n| --- | ---: | --- |\n| 茶 | 1234.50 | A \\| B |\n| 咖啡 | 80.25 | & 与中文 |\n\n- [x] 已核对\n- [ ] 待复核\n\n`a < b`\n\n> 来源：完全合成，没有真实参与者\n"
    return [
        (
            "中文说明.txt",
            "中文收入 1234.50 万元，2026 年，9 件。".encode(),
            ["1234.50", "万元", "2026", "9"],
        ),
        (
            "中文表格.csv",
            "品类,金额（万元）,数量\n茶,1234.50,3\n咖啡,80.25,6\n".encode(),
            ["品类", "1234.50", "80.25", "茶", "咖啡"],
        ),
        (
            "中文记录.json",
            '{"时期": 2026, "金额": 1234.50, "单位": "万元"}'.encode(),
            ["2026", "1234.5", "万元"],
        ),
        ("中文笔记.md", md.encode(), ["1234.50", "80.25", "2026", "合成离线笔记", "\\|"]),
        (
            "中文文档.docx",
            canonical_office(docx.getvalue()),
            ["中文收入", "1234.50", "万元", "2026"],
        ),
        (
            "中文工作簿.xlsx",
            canonical_office(xlsx.getvalue()),
            ["品类", "1234.5", "80.25", "茶", "咖啡"],
        ),
        ("synthetic.pdf", pdf_bytes(), ["1234.50", "CNY", "9 units"]),
    ]


def corpus_cases() -> list[Case]:
    cases = [
        Case(f"format-{Path(name).suffix[1:]}", name, data, tuple(facts))
        for name, data, facts in fixtures()
    ]
    structured = (
        "# Unicode 与转义\n\n"
        "## 2026 年金额（万元）\n\n"
        "| 品类 | 金额 | 备注 |\n| --- | ---: | --- |\n"
        "| 茶 ☕ | 1234.50 | A \\| B |\n| café | 80.25 | 中文 & résumé |\n\n"
        "- [x] 核对 9 件\n- [ ] 保存副本\n\n"
        "`a < b && c > d`\n\n"
        '```python\nprint("<tag> & 中文")\npath = r"C:\\notes\\file"\n```\n\n'
        "\\*literal asterisks\\* and \\`literal backticks\\`\n"
    )
    # A ZIP with known missing required Office members gives a specific parser
    # rejection. Generic conversion_failed is deliberately never an expectation.
    broken = io.BytesIO()
    with zipfile.ZipFile(broken, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
    cases.extend(
        [
            Case(
                "structure-unicode-code",
                "结构与转义.md",
                structured.encode(),
                (
                    "# Unicode 与转义",
                    "## 2026 年金额（万元）",
                    "| 茶 ☕ | 1234.50 | A \\| B |",
                    "| café | 80.25 | 中文 & résumé |",
                    "- [x] 核对 9 件",
                    "`a < b && c > d`",
                    'print("<tag> & 中文")',
                    'path = r"C:\\notes\\file"',
                    "\\*literal asterisks\\*",
                    "\\`literal backticks\\`",
                ),
                exact_markdown=True,
            ),
            Case(
                "empty-pdf",
                "empty.pdf",
                pdf_bytes(""),
                expected_outcome="empty",
                expected_error_code="no_text",
            ),
            Case(
                "malformed-pdf",
                "malformed.pdf",
                b"Author-generated non-PDF bytes.\n",
                expected_outcome="rejected",
                expected_error_code="invalid_pdf",
            ),
            Case(
                "malformed-docx",
                "malformed.docx",
                canonical_office(broken.getvalue()),
                expected_outcome="rejected",
                expected_error_code="invalid_office",
            ),
        ]
    )
    return cases


def api_cases() -> list[Case]:
    cases = corpus_cases()
    note = next(case for case in cases if case.case_id == "format-md")
    for case_id, filename, marker in (
        ("duplicate-name", note.filename, "重复同名文件第二份"),
        ("truncated-name-a", "长" * 100 + "甲.md", "长文件甲"),
        ("truncated-name-b", "长" * 100 + "乙.md", "长文件乙"),
    ):
        cases.append(
            replace(
                note,
                case_id=case_id,
                filename=filename,
                data=note.data + f"\n{marker}\n".encode(),
                required_facts=note.required_facts + (marker,),
            )
        )
    return cases


def case_record(case: Case) -> dict:
    return {
        "case_id": case.case_id,
        "filename": case.filename,
        "input_sha256": sha(case.data),
        "input_bytes": len(case.data),
        "required_facts": list(case.required_facts),
        "expected_outcome": case.expected_outcome,
        "expected_error_code": case.expected_error_code,
        "result": "failed",
    }


def check_input(case: Case) -> None:
    if sha(case.data) != INPUT_SHA256[case.case_id]:
        raise AssertionError(
            f"Input changed for {case.case_id}; review the generator/version, do not rebaseline silently"
        )


def check_markdown(case: Case, data: bytes) -> dict:
    text = data.decode("utf-8")
    missing = [item for item in case.required_facts if item not in text]
    if missing:
        raise AssertionError(f"Missing required facts: {missing!r}")
    if case.exact_markdown and data != case.data:
        raise AssertionError("Markdown passthrough changed source bytes")
    return {
        "missing_facts": missing,
        "markdown_sha256": sha(data),
        "markdown_bytes": len(data),
    }


def expected_error(case: Case, error: Exception | str, metadata: dict) -> bool:
    # A timeout, crash, unavailable dependency, preview error or generic parser
    # failure must fail the case, even when this source is intended to be invalid.
    return (
        case.expected_error_code in {"no_text", "invalid_pdf", "invalid_office"}
        and str(error) == SAFE_ERRORS[case.expected_error_code]
        and metadata.get("version_source") == "worker_reported"
        and metadata.get("engine") == "markitdown"
        and bool(metadata.get("version"))
    )


def evaluate_conversions(out: Path) -> list[dict]:
    rows = []
    (out / "inputs").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="markitdown-evaluation-") as temporary_name:
        temporary = Path(temporary_name).resolve()
        settings = Settings(data_dir=temporary, deployment_mode="local")
        for index, case in enumerate(corpus_cases(), 1):
            row = case_record(case)
            metadata: dict = {}
            started = time.monotonic()
            try:
                check_input(case)
                source = temporary / f"source-{index}{Path(case.filename).suffix}"
                source.write_bytes(case.data)
                input_path = out / "inputs" / f"{case.case_id}{source.suffix}"
                input_path.write_bytes(case.data)
                row["input"] = str(input_path.relative_to(out))
                markdown, _html = run_conversion(
                    source, source.suffix, settings=settings, runtime_metadata=metadata
                )
                row["observed_outcome"] = "success"
                content = markdown.encode("utf-8")
                output = out / f"{Path(case.filename).stem}-{index}.md"
                output.write_bytes(content)
                row["output"] = output.name
                # Record returned bytes even when a content assertion fails.
                row.update(markdown_sha256=sha(content), markdown_bytes=len(content))
                if case.expected_outcome != "success":
                    raise AssertionError(
                        f"Expected {case.expected_outcome}, got a successful conversion"
                    )
                row.update(check_markdown(case, content), result="passed")
            except Exception as exc:
                row.update(error_type=type(exc).__name__, error=str(exc))
                if isinstance(exc, ConversionError) and expected_error(
                    case, exc, metadata
                ):
                    row.update(result="passed", observed_outcome=case.expected_outcome)
                else:
                    row.setdefault("observed_outcome", "unexpected_failure")
            row["execution"] = metadata or {"version_source": "unknown"}
            row["duration_seconds"] = round(time.monotonic() - started, 3)
            rows.append(row)
    return rows


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def wait_job(client, job_id: str, timeout: float = 90) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        require(
            response.status_code == 200,
            f"Job read returned HTTP {response.status_code}",
        )
        job = response.json()
        if job["status"] == "expired":
            return job
        if job["status"] in {"succeeded", "failed"}:
            # Publication/cancellation authority can finish before physical slot
            # cleanup. Compare snapshots only after the real attempt is reaped;
            # otherwise its release timestamp can legitimately change between
            # the job and manifest requests.
            attempts = job.get("attempt_history", [])
            if attempts and attempts[-1].get("physical_released_at") is not None:
                return job
        time.sleep(0.025)
    raise AssertionError(f"Job {job_id} did not terminate within {timeout}s")


def evaluate_api_handoff(out: Path, client, headers: dict) -> dict:
    """Exercise real authenticated routes, scheduler, workers and downloads.

    Each failure remains in its case/archive row; no converter is replaced.
    Download names may collide, so local artifacts use a separate job directory.
    The downloaded ZIP remains Markdown-only; manifests are separate responses.
    """
    cases = api_cases()
    rows = []
    successes = []
    for case in cases:
        row = case_record(case)
        rows.append(row)
        started = time.monotonic()
        try:
            check_input(case)
            submitted = client.post(
                "/api/jobs",
                headers=headers,
                data={"engine": "markitdown"},
                files=[("files", (case.filename, case.data))],
            )
            require(
                submitted.status_code == 202,
                f"Submission returned HTTP {submitted.status_code}",
            )
            payload = submitted.json()
            require(
                not payload["errors"] and len(payload["jobs"]) == 1,
                f"Unexpected admission: {payload}",
            )
            job_id = payload["jobs"][0]["id"]
            row["job_id"] = job_id
            job = wait_job(client, job_id)
            row["status"] = job["status"]
            row["execution"] = job["attempt_history"][-1]["metadata"]
            row["source_sha256"] = job.get("source_sha256")
            require(
                job.get("source_sha256") == sha(case.data),
                "Job source hash differs from uploaded bytes",
            )
            manifest_response = client.get(f"/api/jobs/{job_id}/manifest")
            require(
                manifest_response.status_code == 200,
                f"Manifest returned HTTP {manifest_response.status_code}",
            )
            manifest = manifest_response.json()
            directory = out / "jobs" / job_id
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "manifest.json").write_bytes(manifest_response.content)
            row.update(
                manifest=str((directory / "manifest.json").relative_to(out)),
                manifest_sha256=sha(manifest_response.content),
            )
            require(
                manifest["job_id"] == job_id
                and manifest["filename"] == job["filename"],
                "Manifest identifies another job or filename",
            )
            require(
                manifest["source_sha256"] == sha(case.data),
                "Manifest source hash differs from uploaded bytes",
            )
            require(
                manifest["engine"] == "markitdown"
                and manifest["status"] == job["status"],
                "Manifest engine/status differs from job",
            )
            require(
                manifest["content_quality"] == "not_assessed"
                and manifest["native_document_json"] is False
                and manifest["ocr_enabled"] is False,
                "Manifest promises unsupported quality, native JSON or OCR",
            )
            require(
                manifest["metadata"] == job["metadata"],
                "Manifest metadata differs from job",
            )
            require(
                manifest["submission_snapshot"] == job["submission_snapshot"],
                "Manifest submission snapshot differs from job",
            )
            require(
                manifest["attempt_history"] == job["attempt_history"]
                and len(manifest["attempt_history"]) == 1,
                "Manifest attempt history differs from real job",
            )
            download = client.get(f"/api/jobs/{job_id}/download")
            row["download_status"] = download.status_code
            if case.expected_outcome != "success":
                row["error"] = job.get("error")
                require(
                    job["status"] == "failed"
                    and expected_error(case, job.get("error", ""), row["execution"]),
                    f"Wrong failure outcome: {job.get('error')!r}",
                )
                require(
                    download.status_code == 409,
                    "Failed/empty job incorrectly offers a Markdown download",
                )
                refused = client.post(
                    "/api/jobs/archive", headers=headers, json={"job_ids": [job_id]}
                )
                row["archive_status"] = refused.status_code
                require(
                    refused.status_code == 409,
                    "Failed/empty job incorrectly offers an archive",
                )
                require(
                    not manifest["metadata"].get("markdown_sha256")
                    and not manifest["attempt_history"][0].get("markdown_sha256"),
                    "Failed/empty job claims an output hash",
                )
                row.update(result="passed", observed_outcome=case.expected_outcome)
                continue
            require(
                job["status"] == "succeeded",
                f"Unexpected job outcome: {job.get('error')!r}",
            )
            require(
                download.status_code == 200,
                f"Download returned HTTP {download.status_code}",
            )
            disposition = download.headers.get("content-disposition", "")
            require("filename*=UTF-8''" in disposition, "Download lacks UTF-8 filename")
            filename = unquote(disposition.split("filename*=UTF-8''", 1)[1])
            require(
                PurePosixPath(filename).name == filename
                and "\\" not in filename
                and filename.endswith(".md")
                and len(filename.encode()) <= 180,
                "Unsafe or over-budget Markdown filename",
            )
            content = download.content
            (directory / filename).write_bytes(content)
            row.update(
                download_name=filename,
                output=str((directory / filename).relative_to(out)),
                markdown_sha256=sha(content),
                markdown_bytes=len(content),
            )
            require(
                content == job["markdown"].encode("utf-8"),
                "Download bytes differ from job Markdown",
            )
            row.update(check_markdown(case, content))
            require(
                manifest["metadata"]["source_sha256"] == sha(case.data),
                "Manifest metadata source hash mismatch",
            )
            require(
                manifest["metadata"]["markdown_sha256"] == sha(content)
                and manifest["metadata"]["markdown_bytes"] == len(content),
                "Manifest output identity mismatch",
            )
            require(
                manifest["attempt_history"][0]["markdown_sha256"] == sha(content),
                "Attempt output hash mismatch",
            )
            require(
                manifest["metadata"].get("version_source") == "worker_reported"
                and bool(manifest["metadata"].get("version")),
                "Actual worker version is missing",
            )
            row.update(result="passed", observed_outcome="success")
            successes.append((row, content))
        except Exception as exc:
            row.update(error_type=type(exc).__name__, error=str(exc))
        finally:
            row["duration_seconds"] = round(time.monotonic() - started, 3)
    # Put both duplicate and both truncated names into the same real ZIP, not
    # merely different ten-job chunks where collisions would be irrelevant.
    collision_cases = {
        "format-md",
        "duplicate-name",
        "truncated-name-a",
        "truncated-name-b",
    }
    successes.sort(
        key=lambda item: (item[0]["case_id"] not in collision_cases, item[0]["case_id"])
    )
    archives = []
    for offset in range(0, len(successes), 10):
        group = successes[offset : offset + 10]
        record: dict = {
            "result": "failed",
            "job_ids": [row["job_id"] for row, _ in group],
            "members": [],
        }
        archives.append(record)
        try:
            response = client.post(
                "/api/jobs/archive",
                headers=headers,
                json={"job_ids": record["job_ids"]},
            )
            require(
                response.status_code == 200,
                f"Archive returned HTTP {response.status_code}",
            )
            path = out / f"archive-{len(archives)}.zip"
            path.write_bytes(response.content)
            record.update(output=path.name, sha256=sha(response.content))
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                names = archive.namelist()
                require(
                    len(names) == len(group) == len(set(names)),
                    "Archive member count/uniqueness mismatch",
                )
                for index, ((row, content), name) in enumerate(zip(group, names), 1):
                    expected = f"{Path(row['download_name']).stem}-{row['job_id'][:12]}-{index}.md"
                    require(
                        name == expected
                        and PurePosixPath(name).name == name
                        and "\\" not in name,
                        "Archive name/job association mismatch",
                    )
                    require(
                        name.endswith(".md"), "Archive includes a non-Markdown member"
                    )
                    require(
                        archive.read(name) == content,
                        "ZIP member differs from individual download",
                    )
                    record["members"].append(
                        {
                            "name": name,
                            "job_id": row["job_id"],
                            "source_sha256": row["input_sha256"],
                            "markdown_sha256": sha(content),
                            "manifest": row["manifest"],
                        }
                    )
                    row.update(archive=path.name, archive_member=name)
            record["result"] = "passed"
        except Exception as exc:
            record.update(error_type=type(exc).__name__, error=str(exc))
    checks = []
    by_id = {row["case_id"]: row for row in rows}
    for label, left, right in (
        ("duplicate_names", "format-md", "duplicate-name"),
        ("truncated_names", "truncated-name-a", "truncated-name-b"),
    ):
        a, b = by_id[left], by_id[right]
        passed = bool(
            a.get("archive_member")
            and b.get("archive_member")
            and a["download_name"] == b["download_name"]
            and a["archive"] == b["archive"]
            and a["archive_member"] != b["archive_member"]
            and a["job_id"] != b["job_id"]
            and a["input_sha256"] != b["input_sha256"]
        )
        checks.append({"check": label, "result": "passed" if passed else "failed"})
    all_rows = rows + archives + checks
    return {
        "result": "passed"
        if all(row["result"] == "passed" for row in all_rows)
        else "failed",
        "cases": rows,
        "archives": archives,
        "name_checks": checks,
        "passed": sum(row["result"] == "passed" for row in rows),
        "total": len(rows),
    }


def run_api_handoff(out: Path) -> dict:
    from fastapi.testclient import TestClient
    from markitdown_web.app import create_app

    with tempfile.TemporaryDirectory(prefix="markitdown-export-api-") as temporary:
        app = create_app(
            Settings(
                data_dir=Path(temporary).resolve(),
                deployment_mode="local",
                cookie_secure=False,
                public_origin=None,
                docling_enabled=False,
            )
        )
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            password = "Synthetic-export-only-123!"
            admin = app.state.auth.bootstrap_admin("export-admin", password)
            invite = app.state.auth.create_invite(admin["id"])
            app.state.auth.register(
                "export-user", password, invite["token"], "127.0.0.1"
            )
            marker = {"X-MarkItDown-Request": "1"}
            logged_in = client.post(
                "/api/auth/login",
                headers=marker,
                json={"username": "export-user", "password": password},
            )
            require(logged_in.status_code == 200, "Synthetic user login failed")
            me = client.get("/api/me")
            require(me.status_code == 200, "Synthetic session unavailable")
            headers = {**marker, "X-CSRF-Token": me.json()["csrf_token"]}
            return evaluate_api_handoff(out, client, headers)


def validate_output_directory(out: Path) -> None:
    """Keep generated inputs, outputs, identities and reports out of code pushes."""
    try:
        relative = out.resolve().relative_to(REPO.resolve())
    except ValueError:
        return  # Explicit local path outside the checkout, e.g. a private /tmp dir.
    ignored = subprocess.run(
        [
            "git",
            "check-ignore",
            "--quiet",
            "--",
            str(relative / ".export-corpus-artifact"),
        ],
        cwd=REPO,
        check=False,
    )
    if ignored.returncode != 0:
        raise ValueError(
            "Output inside the repository must be git-ignored; use .venv/synthetic-evaluation or an external local directory"
        )


def prepare_output_directory(out: Path) -> None:
    """Never combine a new/partial run with an older successful report."""
    validate_output_directory(out)
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError(
            "Output directory must be empty; choose a fresh directory. Existing evidence was not changed or removed"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--api-handoff",
        action="store_true",
        help="Also run real authenticated queued conversions and MD/ZIP/manifest downloads",
    )
    args = parser.parse_args()
    prepare_output_directory(args.out)
    rows = evaluate_conversions(args.out)
    handoff: dict = {"result": "not_run"}
    if args.api_handoff:
        try:
            handoff = run_api_handoff(args.out / "api")
        except Exception as exc:
            handoff = {
                "result": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
    source_root = REPO / "packages/markitdown-web/src/markitdown_web"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus_version": CORPUS_VERSION,
        "scope": "7 original format checks plus 1 structured Markdown, 1 empty PDF and 2 malformed documents; author-generated local engineering checks, no broad accuracy or production isolation claim",
        "mode": "local-development",
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "source_tree": subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=REPO, text=True
        ).strip(),
        "tracked_worktree_dirty": bool(
            subprocess.check_output(
                ["git", "status", "--porcelain", "--untracked-files=no"],
                cwd=REPO,
                text=True,
            ).strip()
        ),
        "generator_sha256": sha(Path(__file__).read_bytes()),
        "source_hashes": {
            str(path.relative_to(source_root)): sha(path.read_bytes())
            for path in sorted(source_root.rglob("*.py"))
        },
        "versions": {
            name: version(name)
            for name in (
                "markitdown",
                "markitdown-web",
                "openpyxl",
                "mammoth",
                "markdownify",
                "pandas",
                "pdfminer.six",
                "onnxruntime",
                "markdown-it-py",
                "bleach",
                "fastapi",
                "httpx",
            )
        },
        "versions_scope": "Installed versions in the local worker's Python environment; each executed engine separately reports version/python in execution metadata; this is not a Docling run",
        "python": platform.python_version(),
        "passed": sum(row["result"] == "passed" for row in rows),
        "total": len(rows),
        "cases": rows,
        "api_handoff": handoff,
        "consumer_validation": {
            "github_web": "not_run",
            "obsidian": "not_run",
            "note": "HTTP byte handoff and local preview are not native consumer open/edit acceptance; record actual target-app checks separately",
        },
    }
    (args.out / "evaluation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "total": len(rows),
                "api_handoff": handoff["result"],
                "report": str(args.out / "evaluation.json"),
            }
        )
    )
    raise SystemExit(
        0
        if report["passed"] == len(rows) and handoff["result"] in {"passed", "not_run"}
        else 1
    )


if __name__ == "__main__":
    main()
