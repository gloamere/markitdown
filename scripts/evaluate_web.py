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
import subprocess
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from markitdown_web.conversion import run_conversion
from markitdown_web.state import Settings
from openpyxl import Workbook

REPO = Path(__file__).resolve().parents[1]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pdf_bytes() -> bytes:
    stream = b"BT /F1 16 Tf 50 750 Td (Revenue 1234.50 CNY. Total 9 units.) Tj ET"
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


def fixtures() -> list[tuple[str, bytes, list[str]]]:
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
        ("中文文档.docx", docx.getvalue(), ["中文收入", "1234.50", "万元", "2026"]),
        ("中文工作簿.xlsx", xlsx.getvalue(), ["品类", "1234.5", "80.25", "茶", "咖啡"]),
        ("synthetic.pdf", pdf_bytes(), ["1234.50", "CNY", "9 units"]),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    with tempfile.TemporaryDirectory(prefix="markitdown-evaluation-") as temporary_name:
        # macOS /var is a symlink; resolve only our own fixture directory while
        # retaining the converter's refusal of symlinked inputs and parents.
        temporary = Path(temporary_name).resolve()
        settings = Settings(data_dir=Path(temporary), deployment_mode="local")
        for index, (filename, data, expected) in enumerate(fixtures(), 1):
            source = Path(temporary) / f"source-{index}{Path(filename).suffix}"
            source.write_bytes(data)
            started = time.monotonic()
            row = {
                "filename": filename,
                "input_sha256": sha(data),
                "required_facts": expected,
                "result": "failed",
            }
            try:
                result = run_conversion(source, source.suffix, settings=settings)
                markdown, _html = result
                missing = [item for item in expected if item not in markdown]
                row.update(
                    result="passed" if not missing else "failed",
                    missing_facts=missing,
                    markdown_sha256=sha(markdown.encode()),
                    markdown_bytes=len(markdown.encode()),
                )
                # Save only synthetic Markdown; no real user documents or credentials.
                output = args.out / f"{Path(filename).stem}-{index}.md"
                output.write_text(markdown, encoding="utf-8")
                row["output"] = output.name
            except Exception as exc:
                row["error_type"] = type(exc).__name__
                row["error"] = str(exc)
            row["duration_seconds"] = round(time.monotonic() - started, 3)
            rows.append(row)
    source_root = REPO / "packages/markitdown-web/src/markitdown_web"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "7 author-generated synthetic format checks; no real-user feedback, broad accuracy, capacity or production isolation claim",
        "mode": "local-development",
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "source_hashes": {
            str(path.relative_to(source_root)): sha(path.read_bytes())
            for path in sorted(source_root.rglob("*.py"))
        },
        "versions": {
            name: version(name)
            for name in ("markitdown", "markitdown-web", "markdown-it-py", "bleach")
        },
        "passed": sum(row["result"] == "passed" for row in rows),
        "total": len(rows),
        "cases": rows,
        "consumer_validation": {
            "github_web": "not_run",
            "obsidian": "not_run",
            "note": "Local preview is not consumer acceptance; record actual target-app checks separately",
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
                "report": str(args.out / "evaluation.json"),
            }
        )
    )
    raise SystemExit(0 if report["passed"] == len(rows) else 1)


if __name__ == "__main__":
    main()
