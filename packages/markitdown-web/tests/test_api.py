"""HTTP contract and real conversion coverage for the local-only companion."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

app_module = importlib.import_module("markitdown_web.app")
MIB = 1024 * 1024


def upload(
    client, request_headers, filename, content, content_type="application/octet-stream"
):
    return client.post(
        "/api/convert",
        headers=request_headers,
        files=[("files", (filename, content, content_type))],
    )


def successful_result(response):
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert len(results) == 1
    result = results[0]
    assert result["error"] is None, result
    assert isinstance(result["markdown"], str)
    assert isinstance(result["html"], str)
    return result


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_config_advertises_local_upload_limits(client):
    response = client.get("/api/config")
    assert response.status_code == 200
    config = response.json()
    assert config["max_file_bytes"] == 20 * MIB
    assert config["max_total_bytes"] == 50 * MIB
    assert config["max_files"] == 10
    assert {extension.lstrip(".") for extension in config["extensions"]} == {
        "pdf",
        "docx",
        "xlsx",
        "txt",
        "md",
        "csv",
        "json",
    }


@pytest.mark.parametrize(
    ("filename", "payload", "expected"),
    [
        ("notes.txt", "Tea, café, and coffee ☕".encode(), "café"),
        ("README.md", b"# Heading\n\n**Bold** and `code`.\n", "# Heading"),
        ("inventory.csv", b"Item,Quantity\nTea,3\nCoffee,7\n", "Coffee"),
        ("sample.json", b'{"title":"Example","count":2}', "Example"),
        ("UPPER.TXT", b"Uppercase extensions work", "Uppercase"),
    ],
)
def test_text_formats_convert(client, request_headers, filename, payload, expected):
    result = successful_result(upload(client, request_headers, filename, payload))
    assert Path(result["filename"]).stem == Path(filename).stem
    assert Path(result["filename"]).suffix == Path(filename).suffix.lower()
    assert expected in result["markdown"]


def test_markdown_preview_has_semantic_formatting(client, request_headers):
    result = successful_result(
        upload(
            client, request_headers, "notes.md", b"# Example\n\n**Bold** and `code`\n"
        )
    )
    assert "<h1>Example</h1>" in result["html"]
    assert "<strong>Bold</strong>" in result["html"]
    assert "<code>code</code>" in result["html"]


def test_docx_conversion(client, request_headers, docx_bytes):
    result = successful_result(
        upload(client, request_headers, "sample.docx", docx_bytes)
    )
    assert "Sample document" in result["markdown"]
    assert "tea & coffee" in result["markdown"]


def test_xlsx_conversion(client, request_headers, xlsx_bytes):
    result = successful_result(
        upload(client, request_headers, "sample.xlsx", xlsx_bytes)
    )
    assert "Inventory" in result["markdown"]
    assert "Quantity" in result["markdown"]
    assert "Coffee" in result["markdown"]


def test_pdf_conversion(client, request_headers, pdf_bytes):
    result = successful_result(upload(client, request_headers, "sample.pdf", pdf_bytes))
    assert "Sample PDF document" in result["markdown"]


def test_blank_pdf_reports_ocr_limitation(client, request_headers, blank_pdf_bytes):
    response = upload(client, request_headers, "blank.pdf", blank_pdf_bytes)
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["error"]
    assert result["markdown"] in ("", None)
    assert result["html"] in ("", None)


def test_mixed_batch_keeps_successes_and_order(client, request_headers):
    response = client.post(
        "/api/convert",
        headers=request_headers,
        files=[
            ("files", ("first.txt", b"First document")),
            ("files", ("unsupported.exe", b"not an executable")),
            ("files", ("broken.pdf", b"This is not a PDF")),
            ("files", ("last.md", b"# Last document")),
        ],
    )
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert [result["filename"] for result in results] == [
        "first.txt",
        "unsupported.exe",
        "broken.pdf",
        "last.md",
    ]
    assert results[0]["error"] is None
    assert "First document" in results[0]["markdown"]
    assert results[1]["error"]
    assert results[2]["error"]
    assert results[3]["error"] is None
    assert "Last document" in results[3]["markdown"]


@pytest.mark.parametrize(
    "filename", ["image.png", "page.html", "archive.zip", "no-extension"]
)
def test_unapproved_formats_are_rejected(client, request_headers, filename):
    response = upload(client, request_headers, filename, b"Some harmless bytes")
    assert response.status_code == 200
    assert response.json()["results"][0]["error"]


def test_empty_file_is_a_per_document_error(client, request_headers):
    response = upload(client, request_headers, "empty.txt", b"")
    assert response.status_code == 200
    assert response.json()["results"][0]["error"]


def test_missing_uploads_are_rejected(client, request_headers):
    response = client.post("/api/convert", headers=request_headers)
    assert response.status_code == 400


def test_too_many_files_are_rejected(client, request_headers):
    response = client.post(
        "/api/convert",
        headers=request_headers,
        files=[("files", (f"sample-{number}.txt", b"sample")) for number in range(11)],
    )
    assert response.status_code == 400


def test_ten_files_are_allowed(client, request_headers, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "run_conversion",
        lambda path, suffix: ("converted", "<p>converted</p>"),
    )
    response = client.post(
        "/api/convert",
        headers=request_headers,
        files=[("files", (f"sample-{number}.txt", b"sample")) for number in range(10)],
    )
    assert response.status_code == 200
    assert len(response.json()["results"]) == 10
    assert all(result["error"] is None for result in response.json()["results"])


def test_oversized_file_does_not_discard_other_files(
    client, request_headers, monkeypatch
):
    monkeypatch.setattr(
        app_module,
        "run_conversion",
        lambda path, suffix: ("small document", "<p>small document</p>"),
    )
    response = client.post(
        "/api/convert",
        headers=request_headers,
        files=[
            ("files", ("too-large.txt", b"x" * (20 * MIB + 1))),
            ("files", ("small.txt", b"small")),
        ],
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert results[0]["error"]
    assert results[1]["error"] is None


def test_aggregate_file_size_is_capped(client, request_headers, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "run_conversion",
        lambda path, suffix: ("converted", "<p>converted</p>"),
    )
    # Payload total is just above 50 MiB, but multipart body remains below 51 MiB.
    payload = b"x" * (17 * MIB - 1024)
    response = client.post(
        "/api/convert",
        headers=request_headers,
        files=[("files", (f"part-{number}.txt", payload)) for number in range(3)],
    )
    assert response.status_code == 413


def test_conversion_errors_do_not_expose_tracebacks(
    client, request_headers, monkeypatch
):
    def fail_conversion(path, suffix):
        raise RuntimeError("internal-secret /private/server/path")

    monkeypatch.setattr(app_module, "run_conversion", fail_conversion)
    response = upload(client, request_headers, "broken.txt", b"data")
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["error"]
    assert "Traceback" not in response.text
    assert "internal-secret" not in response.text
    assert "/private/server/path" not in response.text


def test_temporary_uploads_are_removed(client, request_headers, monkeypatch):
    observed_paths = []

    def inspect_temporary_file(path, suffix):
        path = Path(path)
        assert path.is_file()
        assert path.read_bytes() == b"temporary content"
        observed_paths.append(path)
        return "temporary content", "<p>temporary content</p>"

    monkeypatch.setattr(app_module, "run_conversion", inspect_temporary_file)
    successful_result(
        upload(client, request_headers, "temporary.txt", b"temporary content")
    )
    assert observed_paths
    assert all(not path.exists() for path in observed_paths)


def test_temporary_uploads_are_removed_after_failure(
    client, request_headers, monkeypatch
):
    observed_paths = []

    def fail_conversion(path, suffix):
        observed_paths.append(Path(path))
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(app_module, "run_conversion", fail_conversion)
    response = upload(client, request_headers, "broken.txt", b"temporary content")
    assert response.status_code == 200
    assert response.json()["results"][0]["error"]
    assert observed_paths
    assert all(not path.exists() for path in observed_paths)
