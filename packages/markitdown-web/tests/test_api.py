"""Authenticated HTTP contract and real queued document-conversion coverage."""

from __future__ import annotations

import importlib
import io
import zipfile
from pathlib import Path

import pytest
from conftest import wait_job

jobs_module = importlib.import_module("markitdown_web.jobs")
MIB = 1024 * 1024


def upload(
    client, request_headers, filename, content, content_type="application/octet-stream"
):
    return client.post(
        "/api/jobs",
        headers=request_headers,
        files=[("files", (filename, content, content_type))],
    )


def successful_result(client, response):
    assert response.status_code == 202, response.text
    assert response.json()["errors"] == []
    jobs = response.json()["jobs"]
    assert len(jobs) == 1
    assert "markdown" not in jobs[0] and "html" not in jobs[0]
    result = wait_job(client, jobs[0])
    assert result["status"] == "succeeded", result
    assert isinstance(result["created_at"], (int, float))
    assert isinstance(result["expires_at"], (int, float))
    assert result["expires_at"] > result["created_at"]
    assert result["finished_at"] >= result["started_at"] >= result["created_at"]
    assert result["error"] is None, result
    assert isinstance(result["markdown"], str)
    assert isinstance(result["html"], str)
    return result


def failed_result(client, response):
    assert response.status_code == 202, response.text
    assert response.json()["errors"] == []
    jobs = response.json()["jobs"]
    assert len(jobs) == 1
    result = wait_job(client, jobs[0])
    assert result["status"] == "failed", result
    assert result["error"]
    assert not result.get("markdown")
    assert not result.get("html")
    return result


def test_health(anonymous_client):
    response = anonymous_client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_config_advertises_local_upload_limits(anonymous_client):
    response = anonymous_client.get("/api/config")
    assert response.status_code == 200
    config = response.json()
    assert config["has_admin"] is False
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
    result = successful_result(
        client, upload(client, request_headers, filename, payload)
    )
    assert Path(result["filename"]).stem == Path(filename).stem
    assert Path(result["filename"]).suffix == Path(filename).suffix.lower()
    assert expected in result["markdown"]


def test_markdown_preview_has_semantic_formatting(client, request_headers):
    result = successful_result(
        client,
        upload(
            client, request_headers, "notes.md", b"# Example\n\n**Bold** and `code`\n"
        ),
    )
    assert "<h1>Example</h1>" in result["html"]
    assert "<strong>Bold</strong>" in result["html"]
    assert "<code>code</code>" in result["html"]


def test_docx_conversion(client, request_headers, docx_bytes):
    result = successful_result(
        client, upload(client, request_headers, "sample.docx", docx_bytes)
    )
    assert "Sample document" in result["markdown"]
    assert "tea & coffee" in result["markdown"]


def test_xlsx_conversion(client, request_headers, xlsx_bytes):
    result = successful_result(
        client, upload(client, request_headers, "sample.xlsx", xlsx_bytes)
    )
    assert "Inventory" in result["markdown"]
    assert "Quantity" in result["markdown"]
    assert "Coffee" in result["markdown"]


def test_pdf_conversion(client, request_headers, pdf_bytes):
    result = successful_result(
        client, upload(client, request_headers, "sample.pdf", pdf_bytes)
    )
    assert "Sample PDF document" in result["markdown"]


def test_blank_pdf_reports_ocr_limitation(client, request_headers, blank_pdf_bytes):
    result = failed_result(
        client, upload(client, request_headers, "blank.pdf", blank_pdf_bytes)
    )
    assert "OCR" in result["error"]


def test_mixed_batch_keeps_successes_and_order(client, request_headers):
    response = client.post(
        "/api/jobs",
        headers=request_headers,
        files=[
            ("files", ("first.txt", b"First document")),
            ("files", ("unsupported.exe", b"not an executable")),
            ("files", ("broken.pdf", b"This is not a PDF")),
            ("files", ("last.md", b"# Last document")),
        ],
    )
    assert response.status_code == 202, response.text
    batch = response.json()
    assert [job["filename"] for job in batch["jobs"]] == [
        "first.txt",
        "broken.pdf",
        "last.md",
    ]
    assert [error["filename"] for error in batch["errors"]] == ["unsupported.exe"]
    assert batch["errors"][0]["error"]
    results = [wait_job(client, job) for job in batch["jobs"]]
    assert results[0]["status"] == "succeeded"
    assert "First document" in results[0]["markdown"]
    assert results[1]["status"] == "failed"
    assert results[1]["error"]
    assert results[2]["status"] == "succeeded"
    assert "Last document" in results[2]["markdown"]
    assert client.get("/api/me").json()["usage"]["used"] == 3


@pytest.mark.parametrize(
    "filename", ["image.png", "page.html", "archive.zip", "no-extension"]
)
def test_unapproved_formats_are_rejected(client, request_headers, filename):
    response = upload(client, request_headers, filename, b"Some harmless bytes")
    assert response.status_code == 202, response.text
    assert response.json()["jobs"] == []
    assert response.json()["errors"][0]["error"]
    assert client.get("/api/me").json()["usage"]["used"] == 0


def test_empty_file_is_a_per_document_error(client, request_headers):
    response = upload(client, request_headers, "empty.txt", b"")
    assert response.status_code == 202
    assert response.json()["jobs"] == []
    assert response.json()["errors"][0]["error"]


def test_missing_uploads_are_rejected(client, request_headers):
    response = client.post("/api/jobs", headers=request_headers)
    assert response.status_code == 400


def test_too_many_files_are_rejected(client, request_headers):
    response = client.post(
        "/api/jobs",
        headers=request_headers,
        files=[("files", (f"sample-{number}.txt", b"sample")) for number in range(11)],
    )
    assert response.status_code == 400
    assert client.get("/api/jobs").json()["jobs"] == []


def test_ten_files_are_allowed(client, request_headers, monkeypatch):
    monkeypatch.setattr(
        jobs_module,
        "run_conversion",
        lambda path, suffix: ("converted", "<p>converted</p>"),
    )
    response = client.post(
        "/api/jobs",
        headers=request_headers,
        files=[("files", (f"sample-{number}.txt", b"sample")) for number in range(10)],
    )
    assert response.status_code == 202
    assert len(response.json()["jobs"]) == 10
    assert response.json()["errors"] == []
    assert all(
        wait_job(client, job)["status"] == "succeeded"
        for job in response.json()["jobs"]
    )


def test_oversized_file_does_not_discard_other_files(
    client, request_headers, monkeypatch
):
    monkeypatch.setattr(
        jobs_module,
        "run_conversion",
        lambda path, suffix: ("small document", "<p>small document</p>"),
    )
    response = client.post(
        "/api/jobs",
        headers=request_headers,
        files=[
            ("files", ("too-large.txt", b"x" * (20 * MIB + 1))),
            ("files", ("small.txt", b"small")),
        ],
    )
    assert response.status_code == 202
    assert [error["filename"] for error in response.json()["errors"]] == [
        "too-large.txt"
    ]
    assert response.json()["errors"][0]["error"]
    assert [job["filename"] for job in response.json()["jobs"]] == ["small.txt"]
    assert wait_job(client, response.json()["jobs"][0])["status"] == "succeeded"


def test_exact_file_and_aggregate_limits_are_accepted(
    client, request_headers, monkeypatch
):
    monkeypatch.setattr(
        jobs_module,
        "run_conversion",
        lambda path, suffix: ("converted", "<p>converted</p>"),
    )
    response = client.post(
        "/api/jobs",
        headers=request_headers,
        files=[
            ("files", (f"part-{number}.txt", b"x" * (size * MIB)))
            for number, size in enumerate([20, 20, 10])
        ],
    )
    assert response.status_code == 202, response.text
    assert response.json()["errors"] == []
    assert len(response.json()["jobs"]) == 3
    assert all(
        wait_job(client, job)["status"] == "succeeded"
        for job in response.json()["jobs"]
    )


def test_aggregate_file_size_is_capped(client, request_headers, monkeypatch):
    monkeypatch.setattr(
        jobs_module,
        "run_conversion",
        lambda path, suffix: ("converted", "<p>converted</p>"),
    )
    # Payload total is above 50 MiB, while multipart remains below 51 MiB.
    payload = b"x" * (17 * MIB - 1024)
    response = client.post(
        "/api/jobs",
        headers=request_headers,
        files=[("files", (f"part-{number}.txt", payload)) for number in range(3)],
    )
    assert response.status_code == 413
    assert client.get("/api/jobs").json()["jobs"] == []
    assert client.get("/api/me").json()["usage"]["used"] == 0


def test_conversion_errors_do_not_expose_tracebacks(
    client, request_headers, monkeypatch
):
    def fail_conversion(path, suffix):
        raise RuntimeError("internal-secret /private/server/path")

    monkeypatch.setattr(jobs_module, "run_conversion", fail_conversion)
    result = failed_result(
        client, upload(client, request_headers, "broken.txt", b"data")
    )
    response = client.get(f"/api/jobs/{result['id']}")
    for secret in ["Traceback", "internal-secret", "/private/server/path"]:
        assert secret not in response.text
        assert secret not in client.get("/api/jobs").text


@pytest.mark.parametrize("fails", [False, True], ids=["success", "failure"])
def test_retained_uploads_are_private_and_removed_on_delete(
    client, request_headers, monkeypatch, tmp_path, fails
):
    observed_paths = []

    def inspect_source(path, suffix):
        path = Path(path)
        assert path.is_file()
        assert path.read_bytes() == b"temporary content"
        observed_paths.append(path)
        if fails:
            raise RuntimeError("fixture failure")
        return "temporary content", "<p>temporary content</p>"

    monkeypatch.setattr(jobs_module, "run_conversion", inspect_source)
    response = upload(client, request_headers, "temporary.txt", b"temporary content")
    result = (
        failed_result(client, response)
        if fails
        else successful_result(client, response)
    )
    assert observed_paths
    assert all(path.is_relative_to(tmp_path) for path in observed_paths)
    assert all(path.stat().st_mode & 0o077 == 0 for path in observed_paths)
    deletion = client.delete(f"/api/jobs/{result['id']}", headers=request_headers)
    assert deletion.status_code == 200
    assert deletion.json() == {"status": "deleted"}
    assert all(not path.exists() for path in observed_paths)
    assert client.get(f"/api/jobs/{result['id']}").status_code == 404
    assert result["id"] not in {
        job["id"] for job in client.get("/api/jobs").json()["jobs"]
    }
    assert (
        client.delete(f"/api/jobs/{result['id']}", headers=request_headers).status_code
        == 404
    )


def test_download_and_archive_preserve_markdown(client, request_headers):
    first = successful_result(
        client, upload(client, request_headers, "notes.md", b"# First\n")
    )
    second = successful_result(
        client, upload(client, request_headers, "notes.txt", b"Second document")
    )
    download = client.get(f"/api/jobs/{first['id']}/download")
    assert download.status_code == 200
    assert download.text == first["markdown"]
    assert "attachment" in download.headers["content-disposition"]
    assert "notes.md" in download.headers["content-disposition"]
    archive = client.post(
        "/api/jobs/archive",
        headers=request_headers,
        json={"job_ids": [first["id"], second["id"]]},
    )
    assert archive.status_code == 200
    assert archive.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(archive.content)) as result:
        names = result.namelist()
        assert len(names) == len(set(names)) == 2
        assert all(
            name.endswith(".md") and "/" not in name and "\\" not in name
            for name in names
        )
        assert {result.read(name).decode() for name in names} == {
            first["markdown"],
            second["markdown"],
        }
