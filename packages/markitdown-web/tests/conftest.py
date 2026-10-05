"""Self-contained inputs for the local conversion API tests."""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

import pytest
from fastapi.testclient import TestClient

from markitdown_web.app import app


@pytest.fixture
def client():
    # The application intentionally rejects TestClient's default `testserver` host.
    with TestClient(app, base_url="http://127.0.0.1:8000") as test_client:
        yield test_client


@pytest.fixture
def request_headers():
    return {"X-MarkItDown-Request": "1"}


@pytest.fixture
def docx_bytes():
    """A real, minimal OOXML document, with no personal or external resources."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>"
            + escape("Sample document: tea & coffee")
            + "</w:t></w:r></w:p></w:body></w:document>",
        )
    return buffer.getvalue()


@pytest.fixture
def xlsx_bytes():
    from openpyxl import Workbook

    buffer = io.BytesIO()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Inventory"
    sheet.append(["Item", "Quantity"])
    sheet.append(["Tea", 3])
    sheet.append(["Coffee", 7])
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def make_pdf(text: str = "Sample PDF document") -> bytes:
    """Construct a valid one-page PDF with an accurate xref, without PDF libraries."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 18 Tf 50 750 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]
    result = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, content in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode("ascii") + content + b"\nendobj\n")
    startxref = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    result.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{startxref}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(result)


@pytest.fixture
def pdf_bytes():
    return make_pdf()


@pytest.fixture
def blank_pdf_bytes():
    return make_pdf("")
