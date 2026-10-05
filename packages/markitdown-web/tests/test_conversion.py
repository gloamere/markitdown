"""Fast unit coverage for process limits and archive validation."""

from __future__ import annotations

import io
import json
import subprocess
import zipfile
from types import SimpleNamespace

import pytest

from markitdown_web import conversion, worker


def test_conversion_uses_timeout_and_restricted_environment(tmp_path, monkeypatch):
    source = tmp_path / "sample.txt"
    source.write_text("example", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-value")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")
    recorded = {}

    def fake_run(args, **kwargs):
        recorded.update(kwargs)
        assert args[-3:] == [str(source), ".txt", str(tmp_path / "result.json")]
        (tmp_path / "result.json").write_text(
            json.dumps(
                {"markdown": "example", "html": "<p>example</p>", "error": None}
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(conversion.subprocess, "run", fake_run)
    assert conversion.run_conversion(source, ".txt") == ("example", "<p>example</p>")
    assert recorded["timeout"] == 45
    assert recorded["stdin"] == subprocess.DEVNULL
    assert recorded["stdout"] == subprocess.DEVNULL
    assert recorded["stderr"] == subprocess.DEVNULL
    assert "OPENAI_API_KEY" not in recorded["env"]
    assert "HTTPS_PROXY" not in recorded["env"]
    assert recorded["env"]["ORT_DISABLE_TELEMETRY"] == "1"
    assert recorded["env"]["HOME"] == str(tmp_path)
    assert recorded["cwd"] == tmp_path


def test_timed_out_process_becomes_safe_conversion_error(tmp_path, monkeypatch):
    source = tmp_path / "slow.txt"
    source.write_text("sample", encoding="utf-8")

    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(conversion.subprocess, "run", fake_run)
    with pytest.raises(conversion.ConversionError) as error:
        conversion.run_conversion(source, ".txt")
    assert "45" in str(error.value)
    assert str(tmp_path) not in str(error.value)


@pytest.mark.parametrize("returncode", [1, -9])
def test_failed_process_becomes_safe_conversion_error(
    tmp_path, monkeypatch, returncode
):
    monkeypatch.setattr(
        conversion.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=returncode),
    )
    with pytest.raises(conversion.ConversionError):
        conversion.run_conversion(tmp_path / "sample.txt", ".txt")


def test_output_markdown_limit_counts_utf8_bytes(tmp_path, monkeypatch):
    # Fewer than two million characters, but more than two MiB of UTF-8.
    markdown = "文" * (conversion.MAX_MARKDOWN_BYTES // 3 + 1)

    def fake_run(*args, **kwargs):
        (tmp_path / "result.json").write_text(
            json.dumps(
                {"markdown": markdown, "html": "<p>preview</p>", "error": None},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(conversion.subprocess, "run", fake_run)
    with pytest.raises(conversion.ConversionError):
        conversion.run_conversion(tmp_path / "sample.txt", ".txt")


def test_missing_process_output_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(
        conversion.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    with pytest.raises(conversion.ConversionError):
        conversion.run_conversion(tmp_path / "sample.txt", ".txt")


def office_archive(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
        for name, content in entries:
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "name", ["../outside.txt", "/absolute.txt", "nested\\escape.txt", "C:/file.txt"]
)
def test_office_archive_rejects_unsafe_member_paths(name):
    with pytest.raises(conversion.ConversionError):
        worker.validate_office(office_archive([(name, b"harmless")]), ".docx")


@pytest.mark.parametrize(
    "xml",
    [
        b'<!DOCTYPE doc [<!ENTITY file SYSTEM "file:///nonexistent-test-fixture">]><doc>&file;</doc>',
        b'<!ENTITY remote SYSTEM "https://example.invalid/external">',
        "<!DOCTYPE doc><doc/>".encode("utf-16"),
    ],
)
def test_office_archive_rejects_entity_declarations(xml):
    with pytest.raises(conversion.ConversionError):
        worker.validate_office(office_archive([("word/extra.xml", xml)]), ".docx")


def test_office_archive_rejects_compression_bombs():
    with pytest.raises(conversion.ConversionError):
        worker.validate_office(
            office_archive([("word/large.xml", b"x" * (2 * 1024 * 1024))]), ".docx"
        )


def test_office_archive_rejects_wrong_package_type():
    with pytest.raises(conversion.ConversionError):
        worker.validate_office(office_archive([]), ".xlsx")


def test_worker_blocks_network_without_real_connections(monkeypatch):
    import socket

    try:
        import resource
    except ImportError:  # Windows has no resource module.
        pass
    else:
        monkeypatch.setattr(resource, "setrlimit", lambda *args: None)

    # Do not alter this pytest process's resource limits, and restore every socket
    # patch after the test. These calls must fail before creating any connection.
    for target, name in [
        (socket.socket, "connect"),
        (socket.socket, "connect_ex"),
        (socket, "create_connection"),
        (socket, "getaddrinfo"),
    ]:
        monkeypatch.setattr(target, name, getattr(target, name))
    worker.apply_limits()
    with pytest.raises(OSError, match="Network is disabled"):
        socket.create_connection(("example.invalid", 443))
    with pytest.raises(OSError, match="Network is disabled"):
        socket.getaddrinfo("example.invalid", 443)
    with socket.socket() as connection:
        with pytest.raises(OSError, match="Network is disabled"):
            connection.connect(("127.0.0.1", 1))
        with pytest.raises(OSError, match="Network is disabled"):
            connection.connect_ex(("127.0.0.1", 1))


def test_oversized_worker_result_is_rejected_before_parsing(tmp_path, monkeypatch):
    monkeypatch.setattr(conversion, "MAX_RESULT_BYTES", 128)

    def fake_run(*args, **kwargs):
        # Deliberately invalid JSON: the size check must precede parsing.
        (tmp_path / "result.json").write_bytes(b"x" * 129)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(conversion.subprocess, "run", fake_run)
    with pytest.raises(conversion.ConversionError):
        conversion.run_conversion(tmp_path / "sample.txt", ".txt")


def test_output_html_limit_counts_utf8_bytes(tmp_path, monkeypatch):
    assert conversion.MAX_HTML_BYTES == 4 * 1024 * 1024
    monkeypatch.setattr(conversion, "MAX_HTML_BYTES", 8)

    def fake_run(*args, **kwargs):
        (tmp_path / "result.json").write_text(
            json.dumps(
                {"markdown": "sample", "html": "文文文", "error": None}, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(conversion.subprocess, "run", fake_run)
    with pytest.raises(conversion.ConversionError):
        conversion.run_conversion(tmp_path / "sample.txt", ".txt")
