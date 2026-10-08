#!/usr/bin/env python3
"""Reproduce real authenticated API, queue and isolated Docling smoke evidence.

Run with the web application's Python, passing the separate runtime, pinned model
and synthetic fixture directories. No server, external requests, account secrets,
conversion replacements or isolation bypasses are used. Output includes measured
JSON, extracted Markdown and sanitized HTML. Temporary synthetic accounts and the
SQLite/job tree are deleted when the run exits. RSS is sampled, not OS max RSS.
"""
from __future__ import annotations

import os

os.environ["ORT_DISABLE_TELEMETRY"] = "1"

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from markitdown_web import engines
from markitdown_web.app import create_app
from markitdown_web.jobs import JobService
from markitdown_web.state import Settings

BASE_URL = "http://127.0.0.1:8000"
PASSWORD = "Synthetic-benchmark-only-123!"
MARKER = {"X-MarkItDown-Request": "1"}
REPO = Path(__file__).resolve().parents[2]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint() -> dict[str, str]:
    directory = REPO / "packages/markitdown-web/src/markitdown_web"
    names = [
        "engines.py",
        "docling_adapter.py",
        "docling_worker.py",
        "preview.py",
        "jobs.py",
        "state.py",
        "app.py",
        "conversion.py",
        "worker.py",
    ]
    return {name: digest((directory / name).read_bytes()) for name in names}


def eventually(condition, timeout: float = 75):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for benchmark condition")


class Observation:
    """Pass-through observation only: the real subprocesses and guards still run."""

    def __init__(self):
        self.processes: list[dict[str, Any]] = []
        self.service = None
        self.original_popen = subprocess.Popen
        self.original_rss = engines._rss
        self.original_terminate = engines._terminate
        self.original_invoke = engines._invoke
        self.threads: list[threading.Thread] = []
        self.started = time.monotonic()

    def now(self):
        return round(time.monotonic() - self.started, 6)

    def __enter__(self):
        def popen(args, *positional, **kwargs):
            process = self.original_popen(args, *positional, **kwargs)
            arguments = [str(arg) for arg in args]
            docling = str(engines.WORKER) in arguments
            default = "markitdown_web.worker" in arguments
            if not (docling or default):
                return process
            source = Path(arguments[5] if docling else arguments[3])
            record = {
                "pid": process.pid,
                "engine": "docling" if docling else "markitdown",
                "mode": arguments[4] if docling else "convert",
                "job_id": source.parent.name
                if source.name.startswith("source")
                else None,
                "started_at_seconds": self.now(),
                "sampled_peak_rss_bytes": 0,
                "isolation_disabled": False,
            }
            self.processes.append(record)

            def observe():
                while process.poll() is None:
                    try:
                        rss = self.original_rss(process.pid)
                        record["sampled_peak_rss_bytes"] = max(
                            record["sampled_peak_rss_bytes"], rss
                        )
                    except (OSError, ValueError):
                        pass
                    time.sleep(0.01)
                record["exit_observed_at_seconds"] = self.now()
                record["returncode"] = process.returncode
                record["proc_entry_absent_after_reap"] = not Path(
                    f"/proc/{process.pid}"
                ).exists()

            thread = threading.Thread(target=observe, daemon=True)
            self.threads.append(thread)
            thread.start()
            return process

        def terminate(process):
            record = next(
                (item for item in self.processes if item["pid"] == process.pid), None
            )
            if record is not None:
                record["terminate_started_at_seconds"] = self.now()
                record["alive_before_terminate"] = process.poll() is None
                record["slot_present_before_terminate"] = bool(
                    self.service and record["job_id"] in self.service._inflight_engines
                )
            self.original_terminate(process)
            if record is not None:
                record["reaped_at_seconds"] = self.now()
                record["returncode"] = process.returncode
                record["proc_entry_absent_after_reap"] = not Path(
                    f"/proc/{process.pid}"
                ).exists()
                record["slot_present_after_reap"] = bool(
                    self.service and record["job_id"] in self.service._inflight_engines
                )

        def invoke(settings, mode, source, directory, cancelled):
            result = self.original_invoke(settings, mode, source, directory, cancelled)
            if mode == "convert":
                record = next(
                    item
                    for item in reversed(self.processes)
                    if item["job_id"] == source.parent.name and item["mode"] == mode
                )
                record["child_result_contains_html"] = isinstance(
                    result.get("html"), str
                )
                record["child_result_html_sha256"] = digest(
                    result.get("html", "").encode()
                )
            return result

        subprocess.Popen = popen
        engines._terminate = terminate
        engines._invoke = invoke
        return self

    def __exit__(self, *_):
        subprocess.Popen = self.original_popen
        engines._terminate = self.original_terminate
        engines._invoke = self.original_invoke
        for thread in self.threads:
            thread.join(3)

    def conversions(self, job_id):
        return [
            item
            for item in self.processes
            if item["job_id"] == job_id and item["mode"] == "convert"
        ]


def headers(client):
    response = client.get("/api/me")
    assert response.status_code == 200, response.text
    return {**MARKER, "X-CSRF-Token": response.json()["csrf_token"]}


def login(client, name):
    response = client.post(
        "/api/auth/login", headers=MARKER, json={"username": name, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text


def upload(client, filename, data, engine="docling"):
    return client.post(
        "/api/jobs",
        headers=headers(client),
        data={"engine": engine},
        files={"files": (filename, data, "application/pdf")},
    )


def accepted(client, path, engine="docling"):
    response = upload(client, path.name, path.read_bytes(), engine)
    assert response.status_code == 202, response.text
    return response.json()["jobs"][0]


def wait_job(client, job):
    def terminal():
        response = client.get(f"/api/jobs/{job['id']}")
        assert response.status_code == 200, response.text
        result = response.json()
        return (
            result if result["status"] in {"succeeded", "failed", "expired"} else None
        )

    return eventually(terminal)


def quality(markdown: str, fixture: dict, table: list) -> dict:
    snippets = fixture["expected_text"]
    result = {
        "expected_sentinels": len(snippets),
        "sentinels_present": sum(snippet in markdown for snippet in snippets),
        "missing_sentinels": [
            snippet for snippet in snippets if snippet not in markdown
        ],
    }
    if fixture["file"].startswith("columns_table"):
        markers = ["LEFT FIRST", "LEFT LAST", "RIGHT FIRST", "RIGHT LAST", "TABLE END"]
        page_matches = list(re.finditer("LEFT FIRST", markdown))
        orders = []
        for index, match in enumerate(page_matches):
            end = (
                page_matches[index + 1].start()
                if index + 1 < len(page_matches)
                else len(markdown)
            )
            page = markdown[match.start() : end]
            positions = [page.find(marker) for marker in markers]
            orders.append(
                all(position >= 0 for position in positions)
                and positions == sorted(positions)
            )
        rows = [
            [cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in markdown.splitlines()
            if line.strip().startswith("|")
        ]
        matched_rows = sum(rows.count(row) for row in table)
        expected_rows = len(table) * fixture["pages"]
        result.update(
            {
                "column_pages_expected": fixture["pages"],
                "column_pages_in_correct_order": sum(orders),
                "table_rows_exact": matched_rows,
                "table_rows_expected": expected_rows,
                "table_cells_exact_from_matching_rows": matched_rows * len(table[0]),
                "table_cells_expected": expected_rows * len(table[0]),
            }
        )
    return result


def run(args) -> dict:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest = json.loads((args.fixtures / "manifest.json").read_text())
    for fixture in manifest["fixtures"]:
        assert (
            digest((args.fixtures / fixture["file"]).read_bytes()) == fixture["sha256"]
        )
    source_before = fingerprint()
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "harness_python": sys.executable,
        "runtime_python": str(args.python),
        "models": str(args.models),
        "source_sha256": source_before,
        "benchmark_sha256": digest(Path(__file__).read_bytes()),
        "fixture_manifest": manifest,
        "checks": [],
        "conversions": [],
        "scope": "Fresh synthetic authenticated in-process HTTP API, real scheduler and real bounded offline child processes. Sampled RSS, not OS maximum. No historical measurements reused.",
    }

    def check(name, passed, **evidence):
        item = {"case": name, "passed": bool(passed), **evidence}
        report["checks"].append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
        (output / "benchmark.partial.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        return item

    observer = Observation()
    try:
        with tempfile.TemporaryDirectory(
            prefix="private-data-", dir=output
        ) as temporary:
            settings = Settings(
                data_dir=Path(temporary),
                start_workers=False,
                docling_enabled=True,
                docling_python=args.python,
                docling_models=args.models,
            )
            app = create_app(settings)
            # Fresh lifespan: no persistent or preconfigured accounts are used.
            with TestClient(app, base_url=BASE_URL) as alice:
                admin = app.state.auth.bootstrap_admin("benchmark-admin", PASSWORD)
                people = {}
                for name in ("benchmark-alice", "benchmark-bob", "benchmark-charlie"):
                    invite = app.state.auth.create_invite(admin["id"])
                    people[name] = app.state.auth.register(
                        name, PASSWORD, invite["token"], "127.0.0.1"
                    )
                bob = TestClient(app, base_url=BASE_URL)
                charlie = TestClient(app, base_url=BASE_URL)
                login(alice, "benchmark-alice")
                login(bob, "benchmark-bob")
                login(charlie, "benchmark-charlie")
                service = app.state.jobs
                with observer:
                    observer.service = service
                    config = alice.get("/api/config").json()
                    engine = next(
                        item for item in config["engines"] if item["id"] == "docling"
                    )
                    check(
                        "real_engine_config_ready",
                        engine["available"]
                        and engine["max_pages"] == 2
                        and engine["ocr"] is False,
                        engine=engine,
                    )
                    assert engine["available"], engine
                    encrypted = output / "encrypted_synthetic.pdf"
                    subprocess.run(
                        [
                            str(args.python),
                            "-I",
                            "-c",
                            (
                                "from reportlab.pdfgen import canvas; import sys; "
                                "c=canvas.Canvas(sys.argv[1],invariant=1,encrypt='Synthetic-fixture-only'); "
                                "c.drawString(40,700,'Encrypted synthetic fixture'); c.save()"
                            ),
                            str(encrypted),
                        ],
                        check=True,
                        timeout=10,
                        env={"PATH": os.defpath, "ORT_DISABLE_TELEMETRY": "1"},
                    )
                    rejection_cases = [
                        (
                            "reject_three_pages_before_quota_and_admission",
                            "three.pdf",
                            (args.fixtures / "text_3pages.pdf").read_bytes(),
                            "1 至 2 页",
                        ),
                        (
                            "reject_over_10_mib",
                            "large.pdf",
                            b"%PDF-" + b"x" * (10 * 1024**2),
                            "10 MiB",
                        ),
                        (
                            "reject_corrupted_pdf",
                            "corrupted.pdf",
                            b"%PDF-1.7\ncorrupted synthetic",
                            "损坏或加密",
                        ),
                        (
                            "reject_encrypted_pdf",
                            "encrypted.pdf",
                            encrypted.read_bytes(),
                            "损坏或加密",
                        ),
                    ]
                    for name, filename, data, message in rejection_cases:
                        if "three_pages" in name:
                            service.settings = replace(settings, max_queue_jobs=0)
                            with service.db.transaction() as connection:
                                connection.execute(
                                    "UPDATE users SET daily_quota=1 WHERE id=?",
                                    (people["benchmark-alice"]["id"],),
                                )
                                connection.execute(
                                    "INSERT INTO daily_usage(user_id,day,used) VALUES(?,?,1)",
                                    (
                                        people["benchmark-alice"]["id"],
                                        datetime.now(timezone.utc).date().isoformat(),
                                    ),
                                )
                        before = service.get_usage(people["benchmark-alice"]["id"])[
                            "used"
                        ]
                        jobs_before = len(
                            service.list_jobs(people["benchmark-alice"]["id"])
                        )
                        process_count = len(observer.processes)
                        started = time.monotonic()
                        response = upload(alice, filename, data)
                        check(
                            name,
                            response.status_code == 400
                            and message in response.text
                            and service.get_usage(people["benchmark-alice"]["id"])[
                                "used"
                            ]
                            == before
                            and len(service.list_jobs(people["benchmark-alice"]["id"]))
                            == jobs_before
                            and not list(service.jobs_dir.iterdir()),
                            http_status=response.status_code,
                            response=response.json(),
                            input_bytes=len(data),
                            input_sha256=digest(data),
                            wall_seconds=round(time.monotonic() - started, 4),
                            usage_before=before,
                            usage_after=service.get_usage(
                                people["benchmark-alice"]["id"]
                            )["used"],
                            child_modes=[
                                item["mode"]
                                for item in observer.processes[process_count:]
                            ],
                            jobs_before=jobs_before,
                            jobs_after=len(
                                service.list_jobs(people["benchmark-alice"]["id"])
                            ),
                        )
                        service.settings = settings
                        with service.db.transaction() as connection:
                            connection.execute(
                                "UPDATE users SET daily_quota=50 WHERE id=?",
                                (people["benchmark-alice"]["id"],),
                            )
                    with service.db.transaction() as connection:
                        connection.execute("DELETE FROM daily_usage")
                    service.start()
                    for fixture in manifest["fixtures"]:
                        if fixture["pages"] > 2:
                            continue
                        started = time.monotonic()
                        job = accepted(alice, args.fixtures / fixture["file"])
                        result = wait_job(alice, job)
                        eventually(
                            lambda job_id=job["id"]: job_id not in service._inflight
                        )
                        item = {
                            "fixture": fixture["file"],
                            "job_id": job["id"],
                            "engine": result["engine"],
                            "status": result["status"],
                            "error": result.get("error"),
                            "metadata": result["metadata"],
                            "wall_seconds_including_admission": round(
                                time.monotonic() - started, 4
                            ),
                            "source_sha256": fixture["sha256"],
                        }
                        if fixture["text_layer"]:
                            markdown, html = result["markdown"], result["html"]
                            (output / (Path(fixture["file"]).stem + ".md")).write_text(
                                markdown
                            )
                            (
                                output / (Path(fixture["file"]).stem + ".html")
                            ).write_text(html)
                            item.update(
                                {
                                    "quality": quality(
                                        markdown, fixture, manifest["table"]
                                    ),
                                    "markdown_bytes": len(markdown.encode()),
                                    "html_bytes": len(html.encode()),
                                    "markdown_sha256": digest(markdown.encode()),
                                    "html_sha256": digest(html.encode()),
                                }
                            )
                            children = observer.conversions(job["id"])
                            passed = (
                                result["status"] == "succeeded"
                                and result["metadata"]["page_count"] == fixture["pages"]
                                and not item["quality"]["missing_sentinels"]
                            )
                            if fixture["file"].startswith("columns_table"):
                                passed = (
                                    passed
                                    and item["quality"]["column_pages_in_correct_order"]
                                    == fixture["pages"]
                                    and item["quality"][
                                        "table_cells_exact_from_matching_rows"
                                    ]
                                    == item["quality"]["table_cells_expected"]
                                )
                            check("convert_" + fixture["file"], passed, **item)
                            check(
                                "bounded_child_preview_" + fixture["file"],
                                len(children) == 1
                                and children[0].get("child_result_contains_html")
                                and children[0].get("child_result_html_sha256")
                                == item["html_sha256"],
                                job_id=job["id"],
                            )
                        else:
                            check(
                                "ocr_off_image_only_fails_without_fallback",
                                result["status"] == "failed"
                                and "未开启 OCR" in (result.get("error") or "")
                                and not result.get("markdown")
                                and not result.get("html")
                                and not result["metadata"]
                                and all(
                                    child["engine"] == "docling"
                                    for child in observer.conversions(job["id"])
                                ),
                                **item,
                            )
                        report["conversions"].append(item)

                    # Real queue: different users supply two Docling jobs; a third
                    # supplies an ordinary conversion. Both global workers run.
                    service.stop()
                    first = accepted(alice, args.fixtures / "columns_table_2pages.pdf")
                    second = accepted(bob, args.fixtures / "text_1page.pdf")
                    normal = accepted(
                        charlie,
                        args.fixtures / "columns_table_2pages.pdf",
                        "markitdown",
                    )
                    service.start()
                    results = [
                        wait_job(alice, first),
                        wait_job(bob, second),
                        wait_job(charlie, normal),
                    ]
                    eventually(lambda: not service._inflight)
                    a, b, m = [
                        observer.conversions(job["id"])[0]
                        for job in (first, second, normal)
                    ]
                    overlap = max(
                        a["started_at_seconds"], m["started_at_seconds"]
                    ) < min(a["reaped_at_seconds"], m["exit_observed_at_seconds"])
                    check(
                        "docling_queue_serial_with_parallel_markitdown",
                        all(item["status"] == "succeeded" for item in results)
                        and b["started_at_seconds"] >= a["reaped_at_seconds"]
                        and overlap,
                        jobs=[
                            {
                                "id": item["id"],
                                "engine": item["engine"],
                                "status": item["status"],
                            }
                            for item in results
                        ],
                        docling_children_overlap=b["started_at_seconds"]
                        < a["reaped_at_seconds"],
                        ordinary_child_overlaps_docling=overlap,
                    )
                    baseline = results[2]
                    fixture = next(
                        item
                        for item in manifest["fixtures"]
                        if item["file"] == "columns_table_2pages.pdf"
                    )
                    report["markitdown_baseline"] = {
                        "fixture": fixture["file"],
                        "quality": quality(
                            baseline["markdown"], fixture, manifest["table"]
                        ),
                        "worker": m,
                    }
                    (output / "markitdown_columns_table_2pages.md").write_text(
                        baseline["markdown"]
                    )

                    # Cancel a real live child only after it has resident memory.
                    service.stop()
                    cancel_job = accepted(
                        alice, args.fixtures / "columns_table_2pages.pdf"
                    )
                    successor = accepted(bob, args.fixtures / "text_1page.pdf")
                    service.start()
                    child = eventually(
                        lambda: next(
                            (
                                item
                                for item in observer.conversions(cancel_job["id"])
                                if item["sampled_peak_rss_bytes"] >= 100 * 1024**2
                                and "reaped_at_seconds" not in item
                            ),
                            None,
                        )
                    )
                    canceled_at = observer.now()
                    response = alice.post(
                        f"/api/jobs/{cancel_job['id']}/cancel", headers=headers(alice)
                    )
                    canceled = response.json()
                    eventually(lambda: cancel_job["id"] not in service._inflight)
                    following = wait_job(bob, successor)
                    following_child = observer.conversions(successor["id"])[0]
                    job_directory = service.jobs_dir / cancel_job["id"]
                    check(
                        "live_cancellation_kills_reaps_and_holds_slot",
                        response.status_code == 200
                        and canceled["status"] == "failed"
                        and child["returncode"] < 0
                        and child["proc_entry_absent_after_reap"]
                        and child["slot_present_before_terminate"]
                        and child["slot_present_after_reap"]
                        and following_child["started_at_seconds"]
                        >= child["reaped_at_seconds"]
                        and following["status"] == "succeeded"
                        and not (job_directory / "markdown.md").exists()
                        and not (job_directory / "preview.html").exists(),
                        job_id=cancel_job["id"],
                        canceled_at_seconds=canceled_at,
                        cancellation_to_reap_seconds=round(
                            child["reaped_at_seconds"] - canceled_at, 4
                        ),
                        child=child,
                        successor_started_at_seconds=following_child[
                            "started_at_seconds"
                        ],
                        outputs_absent=True,
                    )
                    response = alice.post(
                        f"/api/jobs/{cancel_job['id']}/retry", headers=headers(alice)
                    )
                    retry = response.json()
                    completed_retry = wait_job(alice, retry)
                    check(
                        "real_canceled_job_retry_preserves_engine",
                        response.status_code == 200
                        and retry["engine"] == "docling"
                        and retry["metadata"] == {}
                        and completed_retry["status"] == "succeeded"
                        and completed_retry["metadata"]["page_count"] == 2,
                        retry=response.json(),
                        final_metadata=completed_retry["metadata"],
                    )

                    service.stop()
                    queued = accepted(alice, args.fixtures / "text_1page.pdf")
                    reloaded = JobService(service.db, settings)
                    app.state.jobs = reloaded
                    observer.service = reloaded
                    persisted = reloaded.get_job(
                        people["benchmark-alice"]["id"], cancel_job["id"], True
                    )
                    reloaded.start()
                    after_restart = wait_job(alice, queued)
                    check(
                        "restart_preserves_metadata_and_runs_queued_job",
                        persisted["metadata"] == completed_retry["metadata"]
                        and persisted["engine"] == "docling"
                        and after_restart["status"] == "succeeded"
                        and after_restart["engine"] == "docling",
                        persisted_metadata=persisted["metadata"],
                        queued_final_metadata=after_restart["metadata"],
                    )
                    reloaded.stop()
                    check(
                        "scratch_and_child_cleanup",
                        not list(settings.data_dir.rglob("engine-*"))
                        and not list(settings.data_dir.rglob("docling-*"))
                        and all(
                            not Path(f"/proc/{item['pid']}").exists()
                            for item in observer.processes
                        ),
                        observed_process_count=len(observer.processes),
                    )
                bob.close()
                charlie.close()
    except Exception as exc:
        report["fatal_error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        report["processes"] = observer.processes
        report["source_unchanged_during_benchmark"] = source_before == fingerprint()
        report["source_sha256_after"] = fingerprint()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["private_account_data_cleaned"] = not list(output.glob("private-data-*"))
        report["all_passed"] = (
            not report.get("fatal_error")
            and report["source_unchanged_during_benchmark"]
            and report["private_account_data_cleaned"]
            and all(item["passed"] for item in report["checks"])
        )
        (output / "benchmark.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        print(
            json.dumps(
                {
                    "report": str(output / "benchmark.json"),
                    "all_passed": report["all_passed"],
                }
            ),
            flush=True,
        )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python", type=Path, required=True, help="Separate Docling runtime Python"
    )
    parser.add_argument(
        "--models", type=Path, required=True, help="Verified pinned models directory"
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        required=True,
        help="Synthetic fixtures with manifest.json",
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="New benchmark artifact directory"
    )
    arguments = parser.parse_args()
    for field in ("python", "models", "fixtures", "output"):
        setattr(
            arguments,
            field,
            getattr(arguments, field).absolute()
            if field == "python"
            else getattr(arguments, field).resolve(),
        )
    raise SystemExit(0 if run(arguments)["all_passed"] else 1)
