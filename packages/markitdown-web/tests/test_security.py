"""Local-origin protection, bounded uploads, and inert document previews."""

from __future__ import annotations

import asyncio
import importlib
from html.parser import HTMLParser

import pytest

app_module = importlib.import_module("markitdown_web.app")
MIB = 1024 * 1024


class ElementCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


@pytest.mark.parametrize("header_value", [None, "", "0", "true"])
def test_conversion_requires_custom_request_header(client, header_value):
    headers = {} if header_value is None else {"X-MarkItDown-Request": header_value}
    response = client.post(
        "/api/convert", headers=headers, files={"files": ("sample.txt", b"sample")}
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://127.0.0.1:8001",
        "https://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:8000.evil.example",
        "http://127.0.0.1:8000@evil.example",
        "null",
    ],
)
def test_cross_origin_requests_are_rejected(client, request_headers, origin):
    response = client.post(
        "/api/convert",
        headers={**request_headers, "Origin": origin},
        files={"files": ("sample.txt", b"sample")},
    )
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_matching_origin_is_allowed(client, request_headers, monkeypatch):
    monkeypatch.setattr(
        app_module, "run_conversion", lambda path, suffix: ("sample", "<p>sample</p>")
    )
    response = client.post(
        "/api/convert",
        headers={**request_headers, "Origin": "http://127.0.0.1:8000"},
        files={"files": ("sample.txt", b"sample")},
    )
    assert response.status_code == 200
    assert response.json()["results"][0]["error"] is None


@pytest.mark.parametrize(
    "host", ["evil.example", "localhost.evil.example", "192.0.2.1"]
)
def test_untrusted_hosts_are_rejected(client, host):
    response = client.get("/api/health", headers={"Host": host})
    assert response.status_code in {400, 403}


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8000", "[::1]:8000"])
def test_loopback_hosts_are_allowed(client, host):
    response = client.get("/api/health", headers={"Host": host})
    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/", "/api/health", "/api/config", "/missing"])
def test_responses_have_defense_in_depth_headers(client, path):
    response = client.get(path)
    assert "no-store" in response.headers.get("cache-control", "")
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("referrer-policy") == "no-referrer"
    csp = response.headers.get("content-security-policy", "")
    assert "default-src 'none'" in csp
    assert "'unsafe-inline'" not in csp
    assert "'unsafe-eval'" not in csp
    assert "https:" not in csp
    assert "http:" not in csp
    directives = dict(
        part.strip().split(" ", 1) for part in csp.split(";") if part.strip()
    )
    assert directives.get("object-src", directives["default-src"]) == "'none'"
    assert "frame-ancestors 'none'" in csp


def test_rejected_uploads_are_not_cacheable(client):
    response = client.post("/api/convert", files={"files": ("sample.txt", b"sample")})
    assert response.status_code == 403
    assert "no-store" in response.headers.get("cache-control", "")


@pytest.mark.parametrize(
    "filename",
    [
        "../../report.txt",
        "..\\..\\report.txt",
        "/tmp/report.txt",
        "C:\\Users\\report.txt",
    ],
)
def test_response_filenames_cannot_traverse(
    client, request_headers, monkeypatch, filename
):
    monkeypatch.setattr(
        app_module, "run_conversion", lambda path, suffix: ("sample", "<p>sample</p>")
    )
    response = client.post(
        "/api/convert", headers=request_headers, files={"files": (filename, b"sample")}
    )
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["error"] is None
    assert result["filename"] == "report.txt"
    assert "/" not in result["filename"]
    assert "\\" not in result["filename"]


def test_html_preview_cannot_execute_or_fetch_resources(client, request_headers):
    malicious_markdown = b"""# Still a heading

<script src="https://example.invalid/steal.js">alert(1)</script>
<iframe src="https://example.invalid/frame"></iframe>
<img src="https://example.invalid/track" onerror="alert(2)">
<svg onload="alert(3)"><use href="https://example.invalid/icon"></use></svg>
<style>body { background:url(https://example.invalid/background) }</style>
<a href="javascript:alert(4)" onclick="alert(5)">unsafe</a>

[External link](https://example.invalid/away)
[Mail](mailto:nobody@example.invalid)
[Script](javascript:alert(6))
![Remote image](https://example.invalid/image.png)
![Data image](data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=)
"""
    response = client.post(
        "/api/convert",
        headers=request_headers,
        files={"files": ("untrusted.md", malicious_markdown)},
    )
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["error"] is None
    # Markdown remains available for the user's plain-text download.
    assert "https://example.invalid/away" in result["markdown"]
    parser = ElementCollector()
    parser.feed(result["html"])
    assert any(tag == "h1" for tag, _ in parser.elements)
    prohibited_tags = {
        "script",
        "iframe",
        "img",
        "svg",
        "style",
        "object",
        "embed",
        "link",
        "meta",
        "form",
    }
    for tag, attrs in parser.elements:
        assert tag not in prohibited_tags
        assert not any(name.startswith("on") for name in attrs)
        assert "style" not in attrs
        assert "src" not in attrs
        assert "srcset" not in attrs
        assert "href" not in attrs


def test_oversized_content_length_is_rejected_before_reading(client, request_headers):
    response = client.post(
        "/api/convert",
        headers={
            **request_headers,
            "Content-Length": str(52 * MIB),
            "Content-Type": "multipart/form-data; boundary=testing",
        },
        content=b"",
    )
    assert response.status_code == 413


def test_chunked_upload_is_capped_without_content_length(monkeypatch):
    """Exercise ASGI receive directly so HTTPX cannot combine the input chunks."""
    parser_module = importlib.import_module("starlette.formparsers")
    original_spooled_file = parser_module.SpooledTemporaryFile
    temporary_files = []

    def track_temporary_file(*args, **kwargs):
        temporary = original_spooled_file(*args, **kwargs)
        temporary_files.append(temporary)
        return temporary

    monkeypatch.setattr(parser_module, "SpooledTemporaryFile", track_temporary_file)
    boundary = b"upload-test-boundary"
    preamble = (
        b"--"
        + boundary
        + b'\r\nContent-Disposition: form-data; name="files"; filename="large.txt"'
        b"\r\nContent-Type: text/plain\r\n\r\n"
    )
    chunk = b"x" * MIB
    chunks = iter([preamble, *([chunk] * 52), b"\r\n--" + boundary + b"--\r\n"])
    observed = []
    reads = 0

    async def receive():
        nonlocal reads
        try:
            body = next(chunks)
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}
        reads += 1
        return {"type": "http.request", "body": body, "more_body": True}

    async def send(message):
        observed.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/convert",
        "raw_path": b"/api/convert",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"127.0.0.1:8000"),
            (b"x-markitdown-request", b"1"),
            (b"content-type", b"multipart/form-data; boundary=" + boundary),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
    }
    asyncio.run(app_module.app(scope, receive, send))
    responses = [
        message for message in observed if message["type"] == "http.response.start"
    ]
    assert len(responses) == 1
    assert responses[0]["status"] == 413
    # Reject while receiving, without waiting for the final multipart boundary.
    assert reads < 54
    assert temporary_files
    assert all(temporary.closed for temporary in temporary_files)


@pytest.mark.parametrize(
    "filename",
    [
        "bad\x00name.txt",
        "bad\r\nname.txt",
        "..",
        "<bad>:name?.txt",
        "report\u202etxt.txt",
    ],
)
def test_filename_normalizer_removes_controls_and_unsafe_characters(filename):
    import unicodedata

    sanitized = app_module.safe_filename(filename)
    assert sanitized
    assert sanitized not in {".", ".."}
    assert not any(unicodedata.category(char).startswith("C") for char in sanitized)
    assert not any(char in sanitized for char in '<>:"|?*/\\')


def test_filename_normalizer_bounds_name_length():
    sanitized = app_module.safe_filename("a" * 2000 + ".txt")
    assert sanitized.endswith(".txt")
    assert len(sanitized) <= 180


def test_forwarded_headers_do_not_bypass_local_origin_check(client, request_headers):
    response = client.post(
        "/api/convert",
        headers={
            **request_headers,
            "Origin": "https://evil.example",
            "X-Forwarded-Host": "evil.example",
            "X-Forwarded-Proto": "https",
        },
        files={"files": ("sample.txt", b"sample")},
    )
    assert response.status_code == 403
