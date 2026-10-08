"""Fast unit coverage for process limits and archive validation."""

from __future__ import annotations

import io
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from markitdown_web import conversion, worker


class FakeProcess:
    pid = 99999999

    def __init__(self, returncode=0):
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def wait(self, **kwargs):
        return self.returncode


@pytest.fixture(autouse=True)
def synthetic_source(tmp_path):
    (tmp_path / "sample.txt").write_text("synthetic")


def test_conversion_uses_timeout_and_restricted_environment(tmp_path, monkeypatch):
    source = tmp_path / "sample.txt"
    source.write_text("example", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-value")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")
    recorded = {}

    def fake_popen(args, **kwargs):
        if args[2] == "markitdown_web.preview":
            Path(args[-1]).write_text(json.dumps({"html": "<p>example</p>\n"}))
            return FakeProcess(returncode=0)
        recorded.update(kwargs)
        assert args[-3:-1] == [str(source), ".txt"]
        assert Path(args[-1]).parent.parent == tmp_path
        Path(args[-1]).write_text(
            json.dumps(
                {"markdown": "example", "html": "<p>example</p>", "error": None}
            ),
            encoding="utf-8",
        )
        return FakeProcess(returncode=0)

    monkeypatch.setattr(conversion.subprocess, "Popen", fake_popen)
    assert conversion.run_conversion(source, ".txt") == ("example", "<p>example</p>\n")
    assert recorded["start_new_session"] is True
    assert recorded["close_fds"] is True
    assert recorded["stdin"] == subprocess.DEVNULL
    assert recorded["stdout"] == subprocess.DEVNULL
    assert recorded["stderr"] == subprocess.DEVNULL
    assert "OPENAI_API_KEY" not in recorded["env"]
    assert "HTTPS_PROXY" not in recorded["env"]
    assert recorded["env"]["ORT_DISABLE_TELEMETRY"] == "1"
    assert Path(recorded["env"]["HOME"]).parent == tmp_path
    assert recorded["cwd"].parent == tmp_path
    assert not list(tmp_path.glob("parser-*"))


def test_timed_out_process_becomes_safe_conversion_error(tmp_path, monkeypatch):
    source = tmp_path / "slow.txt"
    source.write_text("sample", encoding="utf-8")

    process = FakeProcess(returncode=None)
    monkeypatch.setattr(conversion.subprocess, "Popen", lambda *a, **k: process)
    ticks = iter([0, 0, 46])
    monkeypatch.setattr(conversion.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(conversion.sandbox, "terminate", lambda child: None)
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
        "Popen",
        lambda *args, **kwargs: FakeProcess(returncode=returncode),
    )
    with pytest.raises(conversion.ConversionError):
        conversion.run_conversion(tmp_path / "sample.txt", ".txt")


def test_output_markdown_limit_counts_utf8_bytes(tmp_path, monkeypatch):
    # Fewer than two million characters, but more than two MiB of UTF-8.
    markdown = "文" * (conversion.MAX_MARKDOWN_BYTES // 3 + 1)

    def fake_popen(args, **kwargs):
        Path(args[-1]).write_text(
            json.dumps(
                {"markdown": markdown, "html": "<p>preview</p>", "error": None},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return FakeProcess(returncode=0)

    monkeypatch.setattr(conversion.subprocess, "Popen", fake_popen)
    with pytest.raises(conversion.ConversionError):
        conversion.run_conversion(tmp_path / "sample.txt", ".txt")


def test_missing_process_output_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(
        conversion.subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(returncode=0),
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

    def fake_popen(args, **kwargs):
        # Deliberately invalid JSON: the size check must precede parsing.
        Path(args[-1]).write_bytes(b"x" * 129)
        return FakeProcess(returncode=0)

    monkeypatch.setattr(conversion.subprocess, "Popen", fake_popen)
    with pytest.raises(conversion.ConversionError):
        conversion.run_conversion(tmp_path / "sample.txt", ".txt")


def test_output_html_limit_counts_utf8_bytes(tmp_path, monkeypatch):
    assert conversion.MAX_HTML_BYTES == 4 * 1024 * 1024
    monkeypatch.setattr(conversion, "MAX_HTML_BYTES", 8)

    def fake_popen(args, **kwargs):
        Path(args[-1]).write_text(
            json.dumps(
                {"markdown": "sample", "html": "child HTML is ignored", "error": None},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return FakeProcess(returncode=0)

    monkeypatch.setattr(conversion.subprocess, "Popen", fake_popen)
    with pytest.raises(conversion.ConversionError):
        conversion.run_conversion(tmp_path / "sample.txt", ".txt")
