"""Versioned synthetic input stability and real queued export handoff checks."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import openpyxl.packaging.core
import openpyxl.writer.excel
import pytest

from markitdown_web.conversion import SAFE_ERRORS, ConversionError

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/evaluate_web.py"
SPEC = importlib.util.spec_from_file_location("export_corpus_evaluation", SCRIPT)
assert SPEC and SPEC.loader
corpus = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = corpus
SPEC.loader.exec_module(corpus)


def test_original_seven_format_helper_keeps_names_and_required_facts():
    fixtures = corpus.fixtures()
    assert [(name, facts) for name, _, facts in fixtures] == [
        ("中文说明.txt", ["1234.50", "万元", "2026", "9"]),
        ("中文表格.csv", ["品类", "1234.50", "80.25", "茶", "咖啡"]),
        ("中文记录.json", ["2026", "1234.5", "万元"]),
        ("中文笔记.md", ["1234.50", "80.25", "2026", "合成离线笔记", "\\|"]),
        ("中文文档.docx", ["中文收入", "1234.50", "万元", "2026"]),
        ("中文工作簿.xlsx", ["品类", "1234.5", "80.25", "茶", "咖啡"]),
        ("synthetic.pdf", ["1234.50", "CNY", "9 units"]),
    ]


def test_versioned_input_hashes_cover_every_case():
    assert corpus.CORPUS_VERSION == "synthetic-export-v1"
    cases = corpus.api_cases()
    assert len(cases) == 14
    assert len({case.case_id for case in cases}) == len(cases)
    assert {
        case.case_id: corpus.sha(case.data) for case in cases
    } == corpus.INPUT_SHA256
    for case in cases:
        corpus.check_input(case)
    changed = replace(cases[0], data=cases[0].data + b"changed")
    with pytest.raises(AssertionError, match="Input changed"):
        corpus.check_input(changed)


def test_generation_is_stable_across_changed_zip_and_core_property_clocks(monkeypatch):
    raw_runs = []
    normalized_runs = []
    real_canonical = corpus.canonical_office
    # More than one ZIP timestamp tick, different month/day and core-property
    # clock. Capture pre-normalization bytes to prove these clocks really varied.
    for year, month, day in ((2002, 3, 4), (2037, 8, 19)):
        raw_packages = []

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(year, month, day, 12, 34, 56, tzinfo=tz)

        def capture(data):
            raw_packages.append(data)
            return real_canonical(data)

        with monkeypatch.context() as patch:
            clock_module = SimpleNamespace(datetime=Clock, timezone=timezone)
            patch.setattr(openpyxl.packaging.core, "datetime", clock_module)
            patch.setattr(openpyxl.writer.excel, "datetime", clock_module)
            patch.setattr(
                zipfile.time,
                "localtime",
                lambda *_: (year, month, day, 12, 34, 56, 0, 1, 0),
            )
            patch.setattr(corpus, "canonical_office", capture)
            cases = corpus.api_cases()
            normalized_runs.append(
                {case.case_id: corpus.sha(case.data) for case in cases}
            )
        raw_runs.append(raw_packages)
        # DOCX, XLSX, malformed DOCX all come through the real generator.
        assert len(raw_packages) == 3
        for data in raw_packages:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                assert {info.date_time for info in archive.infolist()} == {
                    (year, month, day, 12, 34, 56)
                }
        with zipfile.ZipFile(io.BytesIO(raw_packages[1])) as archive:
            xml = archive.read("docProps/core.xml")
            assert (
                xml.count(f"{year:04d}-{month:02d}-{day:02d}T12:34:56Z".encode()) == 2
            )
        for case in cases:
            if Path(case.filename).suffix not in {".xlsx", ".docx"}:
                continue
            with zipfile.ZipFile(io.BytesIO(case.data)) as archive:
                assert archive.namelist() == sorted(archive.namelist())
                assert all(
                    info.date_time == corpus.FIXED_ZIP_TIME
                    for info in archive.infolist()
                )
                assert all(
                    info.compress_type == zipfile.ZIP_STORED
                    for info in archive.infolist()
                )
                if "docProps/core.xml" in archive.namelist():
                    assert (
                        archive.read("docProps/core.xml").count(corpus.FIXED_CORE_TIME)
                        == 2
                    )
    assert all(left != right for left, right in zip(*raw_runs))
    assert normalized_runs[0] == normalized_runs[1] == corpus.INPUT_SHA256


def test_office_normalization_ignores_source_file_mtime(tmp_path):
    # Exercise ZipFile.write's filesystem-mtime path, not just writestr's clock.
    import os

    source = tmp_path / "word.xml"
    source.write_bytes(b"<synthetic>same payload</synthetic>")
    raw = []
    canonical = []
    for stamp in (946684800, 1893456000):
        os.utime(source, (stamp, stamp))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.write(source, "word/document.xml")
        raw.append(buffer.getvalue())
        canonical.append(corpus.canonical_office(buffer.getvalue()))
    assert raw[0] != raw[1]
    assert canonical[0] == canonical[1]


@pytest.mark.parametrize(
    "case_id, outcome, error_code",
    [
        ("empty-pdf", "empty", "no_text"),
        ("malformed-pdf", "rejected", "invalid_pdf"),
        ("malformed-docx", "rejected", "invalid_office"),
    ],
)
def test_negative_expectations_require_specific_observed_worker_error(
    case_id, outcome, error_code
):
    case = next(item for item in corpus.corpus_cases() if item.case_id == case_id)
    assert case.expected_outcome == outcome
    assert case.expected_error_code == error_code
    metadata = {
        "engine": "markitdown",
        "version_source": "worker_reported",
        "version": "1.2.3",
    }
    assert corpus.expected_error(
        case, ConversionError(SAFE_ERRORS[error_code]), metadata
    )
    assert not corpus.expected_error(
        case, ConversionError(SAFE_ERRORS["conversion_failed"]), metadata
    )
    assert not corpus.expected_error(case, ConversionError("转换超时"), metadata)
    assert not corpus.expected_error(case, ConversionError(SAFE_ERRORS[error_code]), {})
    assert not corpus.expected_error(
        case,
        ConversionError(SAFE_ERRORS[error_code]),
        {**metadata, "version_source": "unknown"},
    )


def test_infrastructure_failure_remains_failure_for_all_cases(tmp_path, monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise ConversionError(SAFE_ERRORS["conversion_failed"])

    monkeypatch.setattr(corpus, "run_conversion", unavailable)
    rows = corpus.evaluate_conversions(tmp_path)
    assert len(rows) == 11
    assert all(
        row["result"] == "failed" and row["observed_outcome"] == "unexpected_failure"
        for row in rows
    )
    assert all(row["error"] == SAFE_ERRORS["conversion_failed"] for row in rows)


def test_required_facts_and_exact_structured_markdown_cannot_silently_degrade():
    case = next(
        item
        for item in corpus.corpus_cases()
        if item.case_id == "structure-unicode-code"
    )
    assert corpus.check_markdown(case, case.data)["markdown_sha256"] == corpus.sha(
        case.data
    )
    with pytest.raises(AssertionError, match="Missing required facts"):
        corpus.check_markdown(case, case.data.replace("1234.50".encode(), b"1234"))
    with pytest.raises(AssertionError, match="changed source bytes"):
        corpus.check_markdown(case, case.data + b"\n")


def test_output_artifacts_require_an_ignored_repository_directory(tmp_path):
    corpus.validate_output_directory(tmp_path)
    corpus.validate_output_directory(corpus.REPO / ".venv" / "synthetic-export")
    with pytest.raises(ValueError, match="git-ignored"):
        corpus.validate_output_directory(
            corpus.REPO / "docs" / "published-raw-documents"
        )


def test_real_api_download_zip_and_separate_manifests(
    client, request_headers, other_client, tmp_path
):
    report = corpus.evaluate_api_handoff(tmp_path, client, request_headers)
    assert report["result"] == "passed", json.dumps(
        report, ensure_ascii=False, indent=2
    )
    assert report["passed"] == report["total"] == 14
    assert len(report["archives"]) == 2  # More than the ten-job archive limit.
    assert report["name_checks"] == [
        {"check": "duplicate_names", "result": "passed"},
        {"check": "truncated_names", "result": "passed"},
    ]
    assert [row["observed_outcome"] for row in report["cases"]].count("success") == 11
    assert [row["observed_outcome"] for row in report["cases"]].count("empty") == 1
    assert [row["observed_outcome"] for row in report["cases"]].count("rejected") == 2
    for row in report["cases"]:
        manifest = json.loads((tmp_path / row["manifest"]).read_bytes())
        assert manifest["job_id"] == row["job_id"]
        assert manifest["source_sha256"] == row["input_sha256"]
        assert (
            other_client.get(f"/api/jobs/{row['job_id']}/download").status_code == 404
        )
        assert (
            other_client.get(f"/api/jobs/{row['job_id']}/manifest").status_code == 404
        )
        if row["expected_outcome"] == "success":
            content = (tmp_path / row["output"]).read_bytes()
            assert (
                corpus.sha(content)
                == row["markdown_sha256"]
                == manifest["metadata"]["markdown_sha256"]
            )
            with zipfile.ZipFile(tmp_path / row["archive"]) as archive:
                assert archive.read(row["archive_member"]) == content
                assert all(name.endswith(".md") for name in archive.namelist())


def test_api_infrastructure_errors_are_not_expected_rejections(tmp_path):
    class UnavailableClient:
        def post(self, *_args, **_kwargs):
            return SimpleNamespace(status_code=503)

    report = corpus.evaluate_api_handoff(tmp_path, UnavailableClient(), {})
    assert report["result"] == "failed"
    assert report["passed"] == 0 and report["total"] == 14
    assert all(
        row["error"] == "Submission returned HTTP 503" for row in report["cases"]
    )
    assert not report["archives"]


@pytest.mark.parametrize(
    "tamper",
    ["download", "manifest-source", "manifest-output", "zip-content", "zip-extra-json"],
)
def test_handoff_detects_corrupted_responses(
    client, request_headers, tmp_path, monkeypatch, tamper
):
    """Fault injection around real HTTP responses, never replacement converters."""
    import httpx

    selected = {
        "format-md",
        "duplicate-name",
        "truncated-name-a",
        "truncated-name-b",
        "empty-pdf",
        "malformed-docx",
    }
    cases = [case for case in corpus.api_cases() if case.case_id in selected]
    monkeypatch.setattr(corpus, "api_cases", lambda: cases)

    class TamperedClient:
        def post(self, path, **kwargs):
            response = client.post(path, **kwargs)
            if (
                path != "/api/jobs/archive"
                or response.status_code != 200
                or not tamper.startswith("zip-")
            ):
                return response
            output = io.BytesIO()
            with zipfile.ZipFile(
                io.BytesIO(response.content)
            ) as original, zipfile.ZipFile(output, "w") as changed:
                for index, name in enumerate(original.namelist()):
                    data = original.read(name)
                    if index == 0 and tamper == "zip-content":
                        data += b"\ncorrupted export\n"
                    changed.writestr(name, data)
                if tamper == "zip-extra-json":
                    changed.writestr("manifest.json", b"{}")
            return httpx.Response(
                200, content=output.getvalue(), headers=response.headers
            )

        def get(self, path, **kwargs):
            response = client.get(path, **kwargs)
            if response.status_code != 200:
                return response
            if tamper == "download" and path.endswith("/download"):
                return httpx.Response(
                    200,
                    content=response.content + b"\ncorrupted export\n",
                    headers=response.headers,
                )
            if tamper.startswith("manifest-") and path.endswith("/manifest"):
                payload = response.json()
                if tamper == "manifest-source":
                    payload["source_sha256"] = "0" * 64
                elif payload["status"] == "succeeded":
                    payload["metadata"]["markdown_sha256"] = "0" * 64
                return httpx.Response(200, json=payload, headers=response.headers)
            return response

    report = corpus.evaluate_api_handoff(tmp_path, TamperedClient(), request_headers)
    assert report["result"] == "failed"
    if tamper.startswith("zip-"):
        assert report["passed"] == len(cases)
        assert report["archives"][0]["result"] == "failed"
        assert report["archives"][0]["error_type"] == "AssertionError"
    else:
        successes = [
            row for row in report["cases"] if row["expected_outcome"] == "success"
        ]
        assert all(
            row["result"] == "failed" and row["error_type"] == "AssertionError"
            for row in successes
        )
        if tamper == "download":
            assert all(
                corpus.sha((tmp_path / row["output"]).read_bytes())
                == row["markdown_sha256"]
                for row in successes
            )


def test_output_accepts_new_or_precreated_empty_directory(tmp_path):
    output = tmp_path / "fresh" / "run"
    corpus.prepare_output_directory(output)
    assert output.is_dir() and not list(output.iterdir())
    corpus.prepare_output_directory(output)
    assert not list(output.iterdir())


@pytest.mark.parametrize("artifact", ["evaluation.json", ".partial-evidence"])
def test_cli_rejects_existing_evidence_before_starting_any_work(
    tmp_path, monkeypatch, artifact
):
    output = tmp_path / "previous-run"
    output.mkdir()
    previous = b'{"passed": 11, "total": 11, "api_handoff": {"result": "passed"}}\n'
    (output / artifact).write_bytes(previous)
    (output / "document.md").write_bytes(b"Existing export must not be overwritten")
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    called = []
    monkeypatch.setattr(
        sys, "argv", [str(SCRIPT), "--out", str(output), "--api-handoff"]
    )
    monkeypatch.setattr(
        corpus, "evaluate_conversions", lambda *_: called.append("conversion")
    )
    monkeypatch.setattr(corpus, "run_api_handoff", lambda *_: called.append("api"))
    with pytest.raises(ValueError, match="must be empty"):
        corpus.main()
    assert not called
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before


def test_interrupted_run_cannot_reuse_or_expose_a_stale_success_report(
    tmp_path, monkeypatch
):
    previous = tmp_path / "previous-run"
    previous.mkdir()
    old_report = b'{"passed": 11, "total": 11}\n'
    (previous / "evaluation.json").write_bytes(old_report)
    fresh = tmp_path / "fresh-run"
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--out", str(fresh)])

    def interrupted(out):
        (out / "partial.md").write_bytes(b"partial synthetic output")
        raise RuntimeError("Simulated interruption before report publication")

    monkeypatch.setattr(corpus, "evaluate_conversions", interrupted)
    with pytest.raises(RuntimeError, match="Simulated interruption"):
        corpus.main()
    assert not (fresh / "evaluation.json").exists()
    assert (fresh / "partial.md").read_bytes() == b"partial synthetic output"
    assert (previous / "evaluation.json").read_bytes() == old_report
    # A retry must also use a new directory; it cannot silently mix partial data.
    with pytest.raises(ValueError, match="must be empty"):
        corpus.main()
    assert not (fresh / "evaluation.json").exists()


def test_wait_job_observes_physical_release_before_comparing_snapshots(monkeypatch):
    observations = [
        {"status": "failed", "attempt_history": [{"physical_released_at": None}]},
        {"status": "failed", "attempt_history": [{"physical_released_at": 123.0}]},
    ]
    seen = []

    class ReleasingClient:
        def get(self, _path):
            job = observations[len(seen)]
            seen.append(job)
            return SimpleNamespace(status_code=200, json=lambda: job)

    monkeypatch.setattr(corpus.time, "sleep", lambda _seconds: None)
    assert corpus.wait_job(ReleasingClient(), "synthetic-id") == observations[1]
    assert seen == observations
