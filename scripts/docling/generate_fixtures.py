#!/usr/bin/env python3
"""Generate deterministic, synthetic PDFs with known text and table contents."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

TABLE = [
    ["Product", "Units", "Revenue"],
    ["Atlas", "4", "120"],
    ["Beacon", "7", "280"],
    ["Cedar", "3", "90"],
]


def make_canvas(path: Path) -> canvas.Canvas:
    result = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    result.setAuthor("MarkItDown synthetic fixture generator")
    result.setTitle(path.stem)
    return result


def heading(pdf: canvas.Canvas, title: str, page: int) -> None:
    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(48, 738, title)
    pdf.setFont("Helvetica", 10)
    pdf.drawString(
        48, 716, "Synthetic test document. No personal or external source material."
    )
    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(564, 36, f"Page {page}")


def text_page(pdf: canvas.Canvas, page: int) -> None:
    heading(pdf, "Plain text fixture", page)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(48, 672, f"Section {page}: deterministic extraction")
    pdf.setFont("Helvetica", 11)
    for index, line in enumerate(
        [
            "The quick brown fox jumps over the lazy dog.",
            "Atlas recorded four units and revenue of 120.",
            "Beacon recorded seven units and revenue of 280.",
            "Cedar recorded three units and revenue of 90.",
            f"End of page {page}. This line is intentionally selectable text.",
        ]
    ):
        pdf.drawString(48, 642 - 22 * index, line)
    pdf.showPage()


def columns_table_page(pdf: canvas.Canvas, page: int) -> None:
    heading(pdf, "Columns and table fixture", page)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(48, 674, "Left column")
    pdf.drawString(324, 674, "Right column")
    pdf.setFont("Helvetica", 10)
    left = [
        "LEFT FIRST. Atlas begins the report.",
        "Its figures appear in the table below.",
        "Four units generated revenue of 120.",
        "This paragraph belongs to the left column.",
        "Read this column before the right one.",
        "LEFT LAST. The first column ends here.",
    ]
    right = [
        "RIGHT FIRST. Beacon continues the report.",
        "Seven units generated revenue of 280.",
        "Cedar adds three units and revenue of 90.",
        "This paragraph belongs to the right column.",
        "The next section spans the full page width.",
        "RIGHT LAST. The second column ends here.",
    ]
    for x, lines in [(48, left), (324, right)]:
        for index, line in enumerate(lines):
            pdf.drawString(x, 648 - 19 * index, line)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(48, 484, f"Sales table {page}")
    table = Table(TABLE, colWidths=[240, 120, 156], rowHeights=32)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e4ecf5")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 11),
                ("GRID", (0, 0), (-1, -1), 0.75, colors.HexColor("#46566a")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    table.wrapOn(pdf, 516, 128)
    table.drawOn(pdf, 48, 338)
    pdf.setFont("Helvetica", 11)
    pdf.drawString(48, 308, "TABLE END. Total units: 14. Total revenue: 490.")
    pdf.showPage()


def generate(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    descriptions = [
        ("text_1page.pdf", 1, text_page, ["The quick brown fox", "End of page 1"]),
        (
            "columns_table_1page.pdf",
            1,
            columns_table_page,
            ["LEFT FIRST", "RIGHT FIRST", "Atlas", "Beacon", "Cedar"],
        ),
        (
            "columns_table_2pages.pdf",
            2,
            columns_table_page,
            ["Sales table 1", "Sales table 2", "Total revenue: 490"],
        ),
        (
            "text_3pages.pdf",
            3,
            text_page,
            ["End of page 1", "End of page 2", "End of page 3"],
        ),
    ]
    fixtures = []
    for filename, pages, draw, expected in descriptions:
        pdf = make_canvas(root / filename)
        for page in range(1, pages + 1):
            draw(pdf, page)
        pdf.save()
        fixtures.append(
            {
                "file": filename,
                "pages": pages,
                "text_layer": True,
                "expected_text": expected,
            }
        )
    # Draw an image containing text, then embed it without any PDF text operators.
    image = Image.new("RGB", (1100, 1400), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=32)
    for index, line in enumerate(
        [
            "IMAGE ONLY FIXTURE",
            "This page has no selectable text.",
            "OCR is disabled in this preview.",
            "Atlas 4 120",
            "Beacon 7 280",
            "Cedar 3 90",
        ]
    ):
        draw.text((70, 100 + index * 90), line, font=font, fill="black")
    pdf = make_canvas(root / "image_only_1page.pdf")
    pdf.drawImage(ImageReader(image), 0, 0, width=612, height=792)
    pdf.showPage()
    pdf.save()
    fixtures.append(
        {
            "file": "image_only_1page.pdf",
            "pages": 1,
            "text_layer": False,
            "expected_text": [],
        }
    )
    for item in fixtures:
        data = (root / item["file"]).read_bytes()
        item.update({"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    manifest = {
        "synthetic": True,
        "generator": "generate_fixtures.py",
        "table": TABLE,
        "fixtures": fixtures,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(generate(args.output), indent=2))
