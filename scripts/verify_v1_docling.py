#!/usr/bin/env python3
"""Current v1 local-development API/real Docling smoke; never a production gate.

Run with the application's Python and the separately audited Docling runtime.
All accounts/documents are synthetic. Wrappers only observe real subprocesses;
no converter, quota, admission, security, deadline, or scheduler is replaced.
--crash-worker-dir is internal: starts a real scheduler for deliberate crash QA.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

os.environ["ORT_DISABLE_TELEMETRY"] = "1"
from fastapi.testclient import TestClient
from markitdown_web import engines, sandbox
from markitdown_web.app import create_app
from markitdown_web.jobs import JobService
from markitdown_web.preview import validate_html
from markitdown_web.state import Settings

REPO = Path(__file__).resolve().parents[1]
PASSWORD = "Synthetic-v1-smoke-only-123!"
MARKER = {"X-MarkItDown-Request": "1"}
BASE_URL = "http://127.0.0.1:8000"
HARNESS_VERSION = "v1-docling-smoke-4"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint():
    return {
        str(p.relative_to(REPO)): digest(p.read_bytes())
        for p in sorted(
            (REPO / "packages/markitdown-web/src/markitdown_web").rglob("*.py")
        )
    }


def save(path, data):
    staging = path.with_suffix(path.suffix + ".tmp")
    staging.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    staging.replace(path)


def eventually(condition, timeout=80):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = condition()
        if value:
            return value
        time.sleep(0.025)
    raise AssertionError(f"Condition did not finish in {timeout}s")


class Observation:
    """Pass-through Popen/termination observation; subprocess commands unchanged."""

    def __init__(self):
        self.processes = []
        self.service = None
        self.started = time.monotonic()
        self.threads = []
        self.original_popen = subprocess.Popen
        self.original_terminate = sandbox.terminate

    def now(self):
        return round(time.monotonic() - self.started, 6)

    def __enter__(self):
        def popen(command, *positional, **kwargs):
            process = self.original_popen(command, *positional, **kwargs)
            args = [str(v) for v in command]
            source = output = None
            if str(engines.WORKER) in args:
                index = args.index(str(engines.WORKER))
                engine, mode = "docling", args[index + 1]
                source, output = Path(args[index + 2]), Path(args[index + 3])
            elif "markitdown_web.worker" in args:
                index = args.index("markitdown_web.worker")
                engine, mode = "markitdown", "convert"
                source, output = Path(args[index + 1]), Path(args[index + 3])
            elif "markitdown_web.preview" in args:
                index = args.index("markitdown_web.preview")
                engine, mode = "preview", "preview"
                source, output = Path(args[index + 1]), Path(args[index + 3])
            else:
                return process
            job_dir = source.parent.parent if mode == "preview" else source.parent
            record = {
                "pid": process.pid,
                "engine": engine,
                "mode": mode,
                "job_id": job_dir.name
                if re.fullmatch(r"[0-9a-f]{32}", job_dir.name)
                else None,
                "started_at_seconds": self.now(),
                "sampled_peak_rss_bytes": 0,
                "rss_measurement_scope": "direct-parser-process",
                "argv": args,
                "output_path": str(output),
                "boundary": "local-development; no full filesystem/PID/network namespace claim",
            }
            self.processes.append(record)

            def sample():
                while process.poll() is None:
                    try:
                        record["sampled_peak_rss_bytes"] = max(
                            record["sampled_peak_rss_bytes"],
                            sandbox.process_tree_rss(process.pid),
                        )
                    except (
                        OSError,
                        ValueError,
                        sandbox.ResourceMonitoringUnavailable,
                    ) as exc:
                        # Sampling races with exit; report gaps rather than lose
                        # the observer thread or claim an unobserved zero RSS.
                        record["rss_sample_gaps"] = record.get("rss_sample_gaps", 0) + 1
                        record["last_rss_sample_gap"] = type(exc).__name__
                    time.sleep(0.01)
                record["exit_observed_at_seconds"] = self.now()
                record["returncode"] = process.returncode

            thread = threading.Thread(target=sample, daemon=True)
            self.threads.append(thread)
            thread.start()
            return process

        def terminate(process):
            record = next((r for r in self.processes if r["pid"] == process.pid), None)
            if record is not None:
                record["terminate_started_at_seconds"] = self.now()
                record["alive_before_terminate"] = process.poll() is None
                record["slot_present_before_terminate"] = bool(
                    self.service and record["job_id"] in self.service._inflight_engines
                )
            self.original_terminate(process)
            if record is not None:
                record.update(
                    reaped_at_seconds=self.now(),
                    returncode=process.returncode,
                    proc_entry_absent_after_reap=not Path(
                        f"/proc/{process.pid}"
                    ).exists(),
                    slot_present_after_reap=bool(
                        self.service
                        and record["job_id"] in self.service._inflight_engines
                    ),
                )
                try:
                    result = json.loads(Path(record["output_path"]).read_text())
                    record["result_keys"] = sorted(result)
                    for key in ("markdown", "html"):
                        if isinstance(result.get(key), str):
                            record[key + "_sha256"] = digest(result[key].encode())
                    if result.get("error"):
                        record["error"] = result["error"]
                except (OSError, ValueError):
                    record["result_unreadable"] = True

        subprocess.Popen = popen
        sandbox.terminate = terminate
        return self

    def __exit__(self, *_):
        subprocess.Popen = self.original_popen
        sandbox.terminate = self.original_terminate
        for thread in self.threads:
            thread.join(2)

    def children(self, job_id, engine="docling", mode="convert"):
        return [
            r
            for r in self.processes
            if r["job_id"] == job_id and r["engine"] == engine and r["mode"] == mode
        ]


def settings_for(args, directory):
    return Settings(
        data_dir=directory,
        deployment_mode="local",
        public_origin=None,
        cookie_secure=False,
        start_workers=False,
        docling_enabled=True,
        docling_python=args.python,
        docling_models=args.models,
        global_concurrency=2,
        per_user_concurrency=1,
    )


def headers(client):
    response = client.get("/api/me")
    assert response.status_code == 200, response.text
    return {**MARKER, "X-CSRF-Token": response.json()["csrf_token"]}


def login(client, username):
    response = client.post(
        "/api/auth/login",
        headers=MARKER,
        json={"username": username, "password": PASSWORD},
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
    def read():
        response = client.get(f"/api/jobs/{job['id']}")
        assert response.status_code == 200, response.text
        item = response.json()
        return item if item["status"] in {"succeeded", "failed", "expired"} else None

    return eventually(read)


def quality(markdown, fixture, table):
    result = {
        "missing_sentinels": [s for s in fixture["expected_text"] if s not in markdown],
        "expected_sentinels": len(fixture["expected_text"]),
    }
    if fixture["file"].startswith("columns_table"):
        starts = [m.start() for m in re.finditer("LEFT FIRST", markdown)]
        correct = 0
        for index, start in enumerate(starts):
            page = markdown[
                start : starts[index + 1] if index + 1 < len(starts) else len(markdown)
            ]
            positions = [
                page.find(s)
                for s in (
                    "LEFT FIRST",
                    "LEFT LAST",
                    "RIGHT FIRST",
                    "RIGHT LAST",
                    "TABLE END",
                )
            ]
            correct += all(p >= 0 for p in positions) and positions == sorted(positions)
        rows = [
            [c.strip() for c in line.strip().strip("|").split("|")]
            for line in markdown.splitlines()
            if line.strip().startswith("|")
        ]
        actual_cells = sum(
            min(rows.count(row), fixture["pages"]) * len(row) for row in table
        )
        result.update(
            column_pages_in_correct_order=correct,
            column_pages_expected=fixture["pages"],
            table_cells_exact_from_matching_rows=actual_cells,
            table_cells_expected=sum(len(row) for row in table) * fixture["pages"],
        )
    return result


def snapshot_valid(snapshot):
    if not isinstance(snapshot, dict):
        return False
    unhashed = {k: v for k, v in snapshot.items() if k != "snapshot_sha256"}
    actual = digest(
        json.dumps(
            unhashed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    )
    return actual == snapshot.get("snapshot_sha256")


def crash_worker(args):
    """Real service process deliberately killed by the harness, never a fake converter."""
    app = create_app(settings_for(args, args.crash_worker_dir))
    with TestClient(app, base_url=BASE_URL), Observation() as observer:
        observer.service = app.state.jobs
        app.state.jobs.start()
        while True:
            save(
                args.crash_worker_dir / "crash-observation.json",
                {"processes": observer.processes},
            )
            time.sleep(0.025)


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    report_path = args.output / "report.json"
    manifest = json.loads((args.fixtures / "manifest.json").read_text())
    source_before = fingerprint()
    report = {
        "harness_version": HARNESS_VERSION,
        "harness_sha256": digest(Path(__file__).read_bytes()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "command": [sys.executable, *sys.argv],
        "harness_python": sys.version,
        "runtime_python": str(args.python),
        "models": str(args.models),
        "rss_measurement_scope": "direct-parser-process",
        "host": {
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "affinity_cpus": sorted(os.sched_getaffinity(0)),
            "meminfo": Path("/proc/meminfo").read_text().splitlines()[:3],
        },
        "scope": "Synthetic authenticated TestClient, real SQLite/scheduler, real offline local-development subprocesses. No conversion fakes. Not production namespace isolation evidence.",
        "limitations": [
            "Production bwrap namespace positive gate cannot run in this executor; no bypass attempted.",
            "No live browser or real-user acceptance is covered by this harness.",
            "RSS sampled every approximately 10ms; neither hard peak RSS nor capacity evidence.",
            "Historical 4 CPU / 8 GiB sizing is not asserted as this machine's capacity.",
            "Synthetic short fixtures establish regression checks, not broad document-quality guarantees.",
        ],
        "source_sha256_before": source_before,
        "fixture_manifest": manifest,
        "checks": [],
        "sections": [],
        "conversions": [],
        "processes": [],
    }
    audit = args.python.parent.parent.parent / "runtime-audit.json"
    if audit.exists():
        report["runtime_audit"] = json.loads(audit.read_text())
        report["runtime_audit_sha256"] = digest(audit.read_bytes())
        save(args.output / "runtime-audit.json", report["runtime_audit"])
    observer = Observation()

    def check(case, passed, **evidence):
        item = {"case": case, "passed": bool(passed), **evidence}
        report["checks"].append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
        save(report_path, report)
        return item

    @contextmanager
    def section(name):
        before = len(report["checks"])
        started = time.monotonic()
        try:
            yield
        except Exception as exc:  # noqa: BLE001
            check(
                name + "_exception",
                False,
                error_type=type(exc).__name__,
                error=str(exc),
                traceback=traceback.format_exc(),
            )
        finally:
            report["sections"].append(
                {
                    "name": name,
                    "wall_seconds": round(time.monotonic() - started, 3),
                    "check_count": len(report["checks"]) - before,
                }
            )
            save(report_path, report)

    try:
        with tempfile.TemporaryDirectory(prefix="synthetic-private-") as private:
            settings = settings_for(args, Path(private))
            app = create_app(settings)
            with TestClient(app, base_url=BASE_URL) as alice:
                admin = app.state.auth.bootstrap_admin("smoke-admin", PASSWORD)
                users = {}
                for name in ("alice", "bob", "charlie"):
                    invite = app.state.auth.create_invite(admin["id"])
                    users[name] = app.state.auth.register(
                        "smoke-" + name, PASSWORD, invite["token"], "127.0.0.1"
                    )
                bob, charlie = TestClient(app, base_url=BASE_URL), TestClient(
                    app, base_url=BASE_URL
                )
                clients = {"alice": alice, "bob": bob, "charlie": charlie}
                for name, client in clients.items():
                    login(client, "smoke-" + name)
                service = app.state.jobs
                with observer:
                    observer.service = service
                    with section("availability_and_fixture_integrity"):
                        check(
                            "fixture_sha256",
                            all(
                                digest((args.fixtures / f["file"]).read_bytes())
                                == f["sha256"]
                                for f in manifest["fixtures"]
                            ),
                        )
                        config = alice.get("/api/config").json()
                        enhanced = next(
                            e for e in config["engines"] if e["id"] == "docling"
                        )
                        ordinary = next(
                            e for e in config["engines"] if e["id"] == "markitdown"
                        )
                        check(
                            "available_local_profile_and_limits",
                            enhanced["available"]
                            and enhanced["max_pages"] == 2
                            and enhanced["max_file_bytes"] == 10 * 1024**2
                            and enhanced["ocr"] is False
                            and enhanced.get("production_boundary") is False
                            and ordinary["available"]
                            and ordinary["max_pages"] is None
                            and ordinary["max_file_bytes"] == 20 * 1024**2,
                            docling=enhanced,
                            ordinary=ordinary,
                        )
                    with section("preflight_before_quota_and_admission"):
                        encrypted = args.output / "encrypted-synthetic.pdf"
                        subprocess.run(
                            [
                                str(args.python),
                                "-I",
                                "-c",
                                "from reportlab.pdfgen import canvas;import sys;c=canvas.Canvas(sys.argv[1],invariant=1,encrypt='Synthetic-fixture-only');c.drawString(40,700,'Encrypted synthetic fixture');c.save()",
                                str(encrypted),
                            ],
                            timeout=10,
                            check=True,
                            env={"PATH": os.defpath, "ORT_DISABLE_TELEMETRY": "1"},
                        )
                        cases = [
                            (
                                "three_pages",
                                (args.fixtures / "text_3pages.pdf").read_bytes(),
                                "1 至 2 页",
                            ),
                            ("oversize", b"%PDF-" + b"x" * (10 * 1024**2), "10 MiB"),
                            ("corrupt", b"%PDF-1.7\ncorrupted synthetic", "损坏或加密"),
                            ("encrypted", encrypted.read_bytes(), "损坏或加密"),
                        ]
                        for name, data, message in cases:
                            with section("preflight_" + name):
                                before = service.get_usage(users["alice"]["id"])["used"]
                                jobs_before = len(
                                    service.list_jobs(users["alice"]["id"])
                                )
                                process_start = len(observer.processes)
                                response = upload(alice, name + ".pdf", data)
                                check(
                                    "preflight_" + name,
                                    response.status_code == 400
                                    and message in response.text
                                    and service.get_usage(users["alice"]["id"])["used"]
                                    == before
                                    and len(service.list_jobs(users["alice"]["id"]))
                                    == jobs_before
                                    and not list(service.jobs_dir.iterdir()),
                                    status=response.status_code,
                                    response=response.json(),
                                    bytes=len(data),
                                    sha256=digest(data),
                                    usage_before=before,
                                    usage_after=service.get_usage(users["alice"]["id"])[
                                        "used"
                                    ],
                                    child_modes=[
                                        p["mode"]
                                        for p in observer.processes[process_start:]
                                    ],
                                )
                        # Stronger precedence check with both quota and queue already full.
                        service.settings = replace(settings, max_queue_jobs=0)
                        with service.db.transaction() as connection:
                            connection.execute(
                                "UPDATE users SET daily_quota=1 WHERE id=?",
                                (users["alice"]["id"],),
                            )
                            connection.execute(
                                "INSERT OR REPLACE INTO daily_usage(user_id,day,used) VALUES(?,?,1)",
                                (
                                    users["alice"]["id"],
                                    datetime.now(timezone.utc).date().isoformat(),
                                ),
                            )
                        try:
                            response = upload(
                                alice,
                                "three-full.pdf",
                                (args.fixtures / "text_3pages.pdf").read_bytes(),
                            )
                            check(
                                "preflight_precedes_full_quota_and_full_queue",
                                response.status_code == 400
                                and "1 至 2 页" in response.text,
                                status=response.status_code,
                                response=response.json(),
                            )
                        finally:
                            service.settings = settings
                            with service.db.transaction() as connection:
                                connection.execute(
                                    "UPDATE users SET daily_quota=50 WHERE id=?",
                                    (users["alice"]["id"],),
                                )
                                connection.execute("DELETE FROM daily_usage")
                    service.start()
                    for fixture in manifest["fixtures"]:
                        if fixture["pages"] > 2:
                            continue
                        with section("convert_" + fixture["file"]):
                            start = time.monotonic()
                            job = accepted(alice, args.fixtures / fixture["file"])
                            result = wait_job(alice, job)
                            eventually(
                                lambda job_id=job["id"]: job_id not in service._inflight
                            )
                            item = {
                                "fixture": fixture["file"],
                                "job_id": job["id"],
                                "status": result["status"],
                                "error": result.get("error"),
                                "metadata": result["metadata"],
                                "wall_seconds": round(time.monotonic() - start, 3),
                            }
                            markdown, html = result.get("markdown", ""), result.get(
                                "html", ""
                            )
                            if fixture["text_layer"]:
                                q = quality(markdown, fixture, manifest["table"])
                                item["quality"] = q
                                check(
                                    "convert_" + fixture["file"],
                                    result["status"] == "succeeded"
                                    and result["metadata"].get("page_count")
                                    == fixture["pages"]
                                    and result["metadata"].get("ocr") is False
                                    and not q["missing_sentinels"]
                                    and q.get("column_pages_in_correct_order")
                                    == q.get("column_pages_expected")
                                    and q.get("table_cells_exact_from_matching_rows")
                                    == q.get("table_cells_expected"),
                                    **item,
                                )
                                for suffix, text in (
                                    (".md", markdown),
                                    (".html", html),
                                ):
                                    (
                                        args.output
                                        / (Path(fixture["file"]).stem + suffix)
                                    ).write_text(text)
                                children = observer.children(job["id"])
                                previews = observer.children(
                                    job["id"], "preview", "preview"
                                )
                                check(
                                    "independent_preview_" + fixture["file"],
                                    len(children) == len(previews) == 1
                                    and "html" not in children[0].get("result_keys", [])
                                    and previews[0].get("html_sha256")
                                    == digest(html.encode())
                                    and validate_html(html),
                                    docling_result_keys=children[0].get("result_keys")
                                    if children
                                    else None,
                                    preview_count=len(previews),
                                    html_sha256=digest(html.encode()),
                                )
                                response = alice.get(f"/api/jobs/{job['id']}/manifest")
                                evidence = response.json()
                                attempts = evidence.get("attempt_history", [])
                                check(
                                    "manifest_hashes_" + fixture["file"],
                                    response.status_code == 200
                                    and evidence.get("source_sha256")
                                    == fixture["sha256"]
                                    and len(attempts) == 1
                                    and attempts[0].get("markdown_sha256")
                                    == digest(markdown.encode())
                                    and attempts[0].get("html_sha256")
                                    == digest(html.encode())
                                    and snapshot_valid(
                                        evidence.get("submission_snapshot")
                                    )
                                    and snapshot_valid(attempts[0].get("snapshot"))
                                    and attempts[0]["snapshot"].get("engine_version")
                                    == "2.133.0",
                                    manifest=evidence,
                                )
                                save(
                                    args.output
                                    / (Path(fixture["file"]).stem + "-manifest.json"),
                                    evidence,
                                )
                                codes = {
                                    tail: bob.get(
                                        f"/api/jobs/{job['id']}" + tail
                                    ).status_code
                                    for tail in ("", "/download", "/manifest")
                                }
                                check(
                                    "owner_only_" + fixture["file"],
                                    all(v == 404 for v in codes.values()),
                                    statuses=codes,
                                )
                            else:
                                check(
                                    "ocr_off_image_only_fails",
                                    result["status"] == "failed"
                                    and "未开启 OCR" in (result.get("error") or "")
                                    and not markdown
                                    and not html
                                    and not result["metadata"]
                                    and not observer.children(job["id"], "markitdown"),
                                    **item,
                                )
                            report["conversions"].append(item)
                    with section("exact_size_boundary_and_ordinary_independence"):
                        data = (args.fixtures / "columns_table_2pages.pdf").read_bytes()
                        exact = data + b"\n" * (10 * 1024**2 - len(data))
                        response = upload(alice, "exact-10mib-two-page.pdf", exact)
                        assert response.status_code == 202, response.text
                        result = wait_job(alice, response.json()["jobs"][0])
                        check(
                            "docling_accepts_exact_10mib_two_pages",
                            result["status"] == "succeeded"
                            and result["metadata"].get("page_count") == 2,
                            bytes=len(exact),
                            sha256=digest(exact),
                            status=result["status"],
                            metadata=result["metadata"],
                        )
                        response = upload(
                            charlie,
                            "ordinary-over-10mib.pdf",
                            exact + b"\n",
                            "markitdown",
                        )
                        assert response.status_code == 202, response.text
                        result = wait_job(charlie, response.json()["jobs"][0])
                        check(
                            "ordinary_accepts_pdf_above_docling_size_cap",
                            result["status"] == "succeeded"
                            and result["engine"] == "markitdown",
                            bytes=len(exact) + 1,
                            status=result["status"],
                            metadata=result["metadata"],
                        )
                    with section("queue_serialization_and_ordinary_parallel"):
                        service.stop()
                        service = JobService(
                            service.db, settings, app.state.auth.governance.current
                        )
                        app.state.jobs = service
                        observer.service = service
                        first = accepted(
                            alice, args.fixtures / "columns_table_2pages.pdf"
                        )
                        second = accepted(bob, args.fixtures / "text_1page.pdf")
                        normal = accepted(
                            charlie, args.fixtures / "text_3pages.pdf", "markitdown"
                        )
                        service.start()
                        results = [
                            wait_job(c, j)
                            for c, j in (
                                (alice, first),
                                (bob, second),
                                (charlie, normal),
                            )
                        ]
                        eventually(lambda: not service._inflight)
                        a, b, m = (
                            observer.children(first["id"])[0],
                            observer.children(second["id"])[0],
                            observer.children(normal["id"], "markitdown")[0],
                        )
                        overlap = max(
                            a["started_at_seconds"], m["started_at_seconds"]
                        ) < min(a["reaped_at_seconds"], m["reaped_at_seconds"])
                        check(
                            "different_user_docling_serial_ordinary_parallel",
                            all(r["status"] == "succeeded" for r in results)
                            and b["started_at_seconds"] >= a["reaped_at_seconds"]
                            and overlap,
                            jobs=[
                                {k: r[k] for k in ("id", "engine", "status")}
                                for r in results
                            ],
                            docling_a=[a["started_at_seconds"], a["reaped_at_seconds"]],
                            docling_b=[b["started_at_seconds"], b["reaped_at_seconds"]],
                            ordinary=[m["started_at_seconds"], m["reaped_at_seconds"]],
                            ordinary_overlaps=overlap,
                        )
                        check(
                            "ordinary_pdf_three_pages_unaffected",
                            results[2]["status"] == "succeeded"
                            and "End of page 3" in results[2].get("markdown", ""),
                            metadata=results[2]["metadata"],
                        )
                    with section("live_cancel_and_real_retry"):
                        service.stop()
                        service = JobService(
                            service.db, settings, app.state.auth.governance.current
                        )
                        app.state.jobs = service
                        observer.service = service
                        cancelled = accepted(
                            alice, args.fixtures / "columns_table_2pages.pdf"
                        )
                        successor = accepted(bob, args.fixtures / "text_1page.pdf")
                        service.start()
                        child = eventually(
                            lambda: next(
                                (
                                    p
                                    for p in observer.children(cancelled["id"])
                                    if p["sampled_peak_rss_bytes"] >= 100 * 1024**2
                                    and "reaped_at_seconds" not in p
                                ),
                                None,
                            )
                        )
                        requested = observer.now()
                        response = alice.post(
                            f"/api/jobs/{cancelled['id']}/cancel",
                            headers=headers(alice),
                        )
                        eventually(lambda: cancelled["id"] not in service._inflight)
                        following = wait_job(bob, successor)
                        following_child = observer.children(successor["id"])[0]
                        check(
                            "live_cancel_reaps_before_slot_reuse",
                            response.status_code == 200
                            and child["returncode"] < 0
                            and child["proc_entry_absent_after_reap"]
                            and child["slot_present_before_terminate"]
                            and child["slot_present_after_reap"]
                            and following_child["started_at_seconds"]
                            >= child["reaped_at_seconds"]
                            and following["status"] == "succeeded"
                            and not any(
                                (service.jobs_dir / cancelled["id"] / name).exists()
                                for name in ("markdown.md", "preview.html")
                            ),
                            canceled_response=response.json(),
                            child=child,
                            cancellation_to_reap_seconds=round(
                                child["reaped_at_seconds"] - requested, 4
                            ),
                            successor_start=following_child["started_at_seconds"],
                        )
                        before = service.get_usage(users["alice"]["id"])["used"]
                        response = alice.post(
                            f"/api/jobs/{cancelled['id']}/retry", headers=headers(alice)
                        )
                        assert response.status_code == 200, response.text
                        retry = wait_job(alice, response.json())
                        eventually(lambda: cancelled["id"] not in service._inflight)
                        history = retry["attempt_history"]
                        check(
                            "real_retry_counted_preserves_engine",
                            retry["status"] == "succeeded"
                            and retry["engine"] == "docling"
                            and len(history) == 2
                            and history[0]["state"] == "cancelled"
                            and history[1]["reason"] == "retry"
                            and history[1]["quota_charged"] is True
                            and service.get_usage(users["alice"]["id"])["used"]
                            == before + 1,
                            attempts=history,
                            metadata=retry["metadata"],
                        )
                    with section("real_scheduler_crash_restart"):
                        service.stop()
                        service = JobService(
                            service.db, settings, app.state.auth.governance.current
                        )
                        app.state.jobs = service
                        observer.service = service
                        interrupted = accepted(
                            alice, args.fixtures / "columns_table_2pages.pdf"
                        )
                        before = service.get_usage(users["alice"]["id"])["used"]
                        libc = ctypes.CDLL(None, use_errno=True)
                        old_subreaper = ctypes.c_int()
                        assert libc.prctl(37, ctypes.byref(old_subreaper), 0, 0, 0) == 0
                        assert libc.prctl(36, 1, 0, 0, 0) == 0
                        process = None
                        orphan_pid = None
                        try:
                            process = subprocess.Popen(
                                [
                                    sys.executable,
                                    str(Path(__file__).resolve()),
                                    "--python",
                                    str(args.python),
                                    "--models",
                                    str(args.models),
                                    "--crash-worker-dir",
                                    private,
                                ],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                start_new_session=True,
                            )

                            def live_crash_child():
                                try:
                                    observations = json.loads(
                                        (
                                            Path(private) / "crash-observation.json"
                                        ).read_text()
                                    )
                                except (OSError, ValueError):
                                    return None
                                return next(
                                    (
                                        p
                                        for p in observations["processes"]
                                        if p["job_id"] == interrupted["id"]
                                        and p["mode"] == "convert"
                                        and p["sampled_peak_rss_bytes"]
                                        >= 100 * 1024**2
                                    ),
                                    None,
                                )

                            crash_child = eventually(live_crash_child, 20)
                            orphan_pid = crash_child["pid"]
                            running = alice.get(f"/api/jobs/{interrupted['id']}").json()
                            process.kill()
                            process.wait(timeout=5)

                            def reap_orphan():
                                pid, status = os.waitpid(orphan_pid, os.WNOHANG)
                                return (
                                    {
                                        "pid": pid,
                                        "exit_code": os.waitstatus_to_exitcode(status),
                                    }
                                    if pid
                                    else None
                                )

                            orphan = eventually(reap_orphan, 8)
                            check(
                                "real_scheduler_kill_parent_death_reaps_child",
                                process.returncode == -signal.SIGKILL
                                and orphan["exit_code"] == -signal.SIGKILL
                                and not Path(f"/proc/{orphan_pid}").exists()
                                and running["status"] == "running",
                                scheduler_exit=process.returncode,
                                child=crash_child,
                                orphan_reap=orphan,
                                note="Harness temporarily acts as child subreaper solely to reap this deliberately orphaned real worker; no conversion restriction changes",
                            )
                            service = JobService(
                                service.db, settings, app.state.auth.governance.current
                            )
                            app.state.jobs = service
                            observer.service = service
                            service.start()
                            recovered = wait_job(alice, interrupted)
                            eventually(lambda: not service._inflight)
                            history = recovered["attempt_history"]
                            check(
                                "restart_recovery_new_counted_attempt_and_version",
                                recovered["status"] == "succeeded"
                                and recovered["attempts"] == 2
                                and len(history) == 2
                                and history[0]["state"] == "interrupted"
                                and history[0]["error_code"] == "service_restart"
                                and history[1]["reason"] == "restart_recovery"
                                and history[1]["quota_charged"] is True
                                and service.get_usage(users["alice"]["id"])["used"]
                                == before + 1
                                and history[0]["snapshot"]
                                == running["attempt_history"][0]["snapshot"]
                                and all(
                                    snapshot_valid(h["snapshot"])
                                    and h["snapshot"].get("engine_version") == "2.133.0"
                                    for h in history
                                ),
                                quota_before_restart=before,
                                quota_after_restart=service.get_usage(
                                    users["alice"]["id"]
                                )["used"],
                                attempts=history,
                                metadata=recovered["metadata"],
                            )
                        finally:
                            if process and process.poll() is None:
                                process.kill()
                                process.wait(timeout=5)
                            if orphan_pid and Path(f"/proc/{orphan_pid}").exists():
                                try:
                                    os.kill(orphan_pid, signal.SIGKILL)
                                    os.waitpid(orphan_pid, 0)
                                except (ProcessLookupError, ChildProcessError):
                                    pass
                            libc.prctl(36, old_subreaper.value, 0, 0, 0)
                    with section("cleanup"):
                        service.stop()
                        service = JobService(
                            service.db, settings, app.state.auth.governance.current
                        )
                        app.state.jobs = service
                        observer.service = service
                        leftovers = [
                            str(p.relative_to(settings.data_dir))
                            for pattern in (
                                "engine-*",
                                "docling-*",
                                "parser-*",
                                "preview-*",
                            )
                            for p in settings.data_dir.rglob(pattern)
                        ]
                        check(
                            "scratch_and_child_cleanup",
                            not leftovers
                            and all(
                                not Path(f"/proc/{p['pid']}").exists()
                                for p in observer.processes
                            ),
                            leftovers=leftovers,
                            observed_process_count=len(observer.processes),
                        )
                bob.close()
                charlie.close()
    except Exception as exc:  # noqa: BLE001
        check(
            "fatal_harness_setup_or_teardown",
            False,
            error_type=type(exc).__name__,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
    finally:
        report["processes"] = observer.processes
        report["source_sha256_after"] = fingerprint()
        check(
            "source_unchanged_during_run",
            source_before == report["source_sha256_after"],
        )
        expected = {
            "fixture_sha256",
            "available_local_profile_and_limits",
            "preflight_three_pages",
            "preflight_oversize",
            "preflight_corrupt",
            "preflight_encrypted",
            "preflight_precedes_full_quota_and_full_queue",
            "ocr_off_image_only_fails",
            "docling_accepts_exact_10mib_two_pages",
            "ordinary_accepts_pdf_above_docling_size_cap",
            "different_user_docling_serial_ordinary_parallel",
            "ordinary_pdf_three_pages_unaffected",
            "live_cancel_reaps_before_slot_reuse",
            "real_retry_counted_preserves_engine",
            "real_scheduler_kill_parent_death_reaps_child",
            "restart_recovery_new_counted_attempt_and_version",
            "scratch_and_child_cleanup",
            "source_unchanged_during_run",
        }
        for fixture in manifest["fixtures"]:
            if fixture["pages"] <= 2 and fixture["text_layer"]:
                expected.update(
                    prefix + fixture["file"]
                    for prefix in (
                        "convert_",
                        "independent_preview_",
                        "manifest_hashes_",
                        "owner_only_",
                    )
                )
        report["planned_cases_not_reached"] = sorted(
            expected - {c["case"] for c in report["checks"]}
        )
        for name in report["planned_cases_not_reached"]:
            check(
                name,
                False,
                not_run=True,
                reason="Dependent section failed; see its recorded exception",
            )
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["all_passed"] = bool(report["checks"]) and all(
            c["passed"] for c in report["checks"]
        )
        report["pass_count"] = sum(c["passed"] for c in report["checks"])
        report["fail_count"] = sum(not c["passed"] for c in report["checks"])
        report["synthetic_private_data_removed"] = (
            not Path(private).exists() if "private" in locals() else True
        )
        save(report_path, report)
        print(
            json.dumps(
                {
                    "report": str(report_path),
                    "passed": report["pass_count"],
                    "failed": report["fail_count"],
                    "all_passed": report["all_passed"],
                }
            ),
            flush=True,
        )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--crash-worker-dir", type=Path)
    args = parser.parse_args()
    args.python = args.python.absolute()  # Keep venv executable, not its realpath.
    args.models = args.models.resolve()
    if args.crash_worker_dir:
        crash_worker(args)
    else:
        if not args.fixtures or not args.output:
            parser.error("--fixtures and --output are required for a verification run")
        args.fixtures, args.output = args.fixtures.resolve(), args.output.resolve()
        raise SystemExit(0 if run(args)["all_passed"] else 1)
