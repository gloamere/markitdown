"""Isolated authenticated clients and synthetic real-format conversion inputs."""

from __future__ import annotations

import io
import os
import time
import zipfile
from xml.sax.saxutils import escape

import pytest
from fastapi.testclient import TestClient

# Set the ONNX opt-out before importing any application/conversion modules.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"

from markitdown_web.app import create_app  # noqa: E402
from markitdown_web.state import Settings  # noqa: E402

BASE_URL = "http://127.0.0.1:8000"
PASSWORD = "Synthetic-passphrase-123!"
REQUEST_MARKER = {"X-MarkItDown-Request": "1"}


def login(test_client, username, password=PASSWORD):
    response = test_client.post(
        "/api/auth/login",
        headers=REQUEST_MARKER,
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()


def headers_for(test_client):
    response = test_client.get("/api/me")
    assert response.status_code == 200, response.text
    return {**REQUEST_MARKER, "X-CSRF-Token": response.json()["csrf_token"]}


def wait_job(test_client, job_or_id, timeout=10):
    job_id = job_or_id["id"] if isinstance(job_or_id, dict) else job_or_id
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = test_client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] in {"succeeded", "failed", "expired"}:
            return job
        time.sleep(0.02)
    pytest.fail(f"Job {job_id} did not complete within {timeout}s: {job}")


@pytest.fixture
def web_app(tmp_path):
    return create_app(Settings(data_dir=tmp_path, start_workers=True))


@pytest.fixture
def anonymous_client(web_app):
    # Each test owns a fresh database, scheduler, and complete lifespan cleanup.
    with TestClient(web_app, base_url=BASE_URL) as test_client:
        yield test_client


@pytest.fixture
def admin(web_app, anonymous_client):
    return web_app.state.auth.bootstrap_admin("test-admin", PASSWORD)


@pytest.fixture
def test_user(web_app, admin):
    invite = web_app.state.auth.create_invite(admin["id"])
    return web_app.state.auth.register(
        "test-user", PASSWORD, invite["token"], "127.0.0.1"
    )


@pytest.fixture
def client(anonymous_client, test_user):
    login(anonymous_client, test_user["username"])
    return anonymous_client


@pytest.fixture
def admin_client(web_app, anonymous_client, admin):
    # The anonymous_client fixture already owns this application's lifespan.
    test_client = TestClient(web_app, base_url=BASE_URL)
    login(test_client, admin["username"])
    yield test_client
    test_client.close()


@pytest.fixture
def other_client(web_app, anonymous_client, admin):
    invite = web_app.state.auth.create_invite(admin["id"])
    user = web_app.state.auth.register(
        "other-user", PASSWORD, invite["token"], "127.0.0.1"
    )
    test_client = TestClient(web_app, base_url=BASE_URL)
    login(test_client, user["username"])
    yield test_client
    test_client.close()


@pytest.fixture
def request_headers(client):
    return headers_for(client)


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
