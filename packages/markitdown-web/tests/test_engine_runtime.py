"""Offline contract tests; real model runs live in scripts/docling/benchmark.py."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from markitdown_web import docling_adapter, docling_worker, engines
from markitdown_web.conversion import ConversionError, validated_preview


@pytest.fixture
def settings(tmp_path):
    # macOS /var is commonly a symlink; trusted runtime fixtures use canonical paths.
    tmp_path = tmp_path.resolve()
    return SimpleNamespace(
        data_dir=tmp_path,
        docling_enabled=True,
        docling_python=Path(sys.executable),
        docling_models=tmp_path / "models",
        max_file_bytes=20 * 1024**2,
    )


def test_default_has_no_docling_imports(settings, monkeypatch):
    monkeypatch.setattr(settings, "docling_enabled", False)
    config = engines.engine_config(settings)
    assert config[0]["available"] is True
    assert config[1]["available"] is False
    assert config[1]["max_pages"] == 2
    assert config[1]["max_file_bytes"] == 10 * 1024**2
    assert config[1]["timeout_seconds"] == 60
    assert config[1]["ocr"] is False
    assert "torch" not in sys.modules
    assert "docling.document_converter" not in sys.modules


@pytest.mark.parametrize("engine", ["", "unknown", "Docling", None])
def test_unknown_engine(engine, settings):
    with pytest.raises(ConversionError, match="未知"):
        engines.ensure_engine_available(engine, settings)


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_platform_fails_closed_before_probe_or_parsing(settings, monkeypatch, platform):
    # Replace this module's platform view, never Python's global sys.platform.
    monkeypatch.setattr(engines, "sys", SimpleNamespace(platform=platform))
    for name in ("_model_signature", "_verify_models", "_invoke"):
        monkeypatch.setattr(
            engines,
            name,
            lambda *a: pytest.fail("Unsupported platform reached runtime"),
        )
    with pytest.raises(ConversionError, match="尚未就绪"):
        engines.ensure_engine_available("docling", settings)
    with pytest.raises(ConversionError, match="尚未就绪"):
        engines.validate_engine_uploads(
            "docling", [{"suffix": ".pdf", "data": b"%PDF-test"}], settings
        )
    with pytest.raises(ConversionError, match="尚未就绪"):
        engines.run_docling_conversion(
            settings.data_dir / "source.pdf", settings, lambda: False
        )
    config = engines.engine_config(settings)
    assert config[0]["available"] is True
    assert config[1]["available"] is False and "尚未就绪" in config[1]["reason"]
    assert not list(settings.data_dir.glob("engine-*"))


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_worker_refuses_unsupported_platform_before_limits(monkeypatch, platform):
    monkeypatch.setattr(docling_worker, "sys", SimpleNamespace(platform=platform))
    # A regression must never install real seccomp/resource limits in pytest.
    monkeypatch.setattr(
        docling_worker.ctypes,
        "CDLL",
        lambda *a, **k: pytest.fail("OS enforcement reached"),
    )
    with pytest.raises(
        RuntimeError, match="Linux resource and network enforcement required"
    ):
        docling_worker.restrict("probe")


def test_model_symlink_rejected(settings, monkeypatch):
    # Reach the path validation itself even when this unit test runs on macOS.
    monkeypatch.setattr(engines, "sys", SimpleNamespace(platform="linux"))
    real = settings.data_dir / "real"
    real.mkdir()
    settings.docling_models.symlink_to(real, target_is_directory=True)
    with pytest.raises(ConversionError):
        engines._paths(settings)


def test_model_signature_size_type_and_hash(settings, monkeypatch):
    settings.docling_models.mkdir()
    asset = settings.docling_models / "asset.bin"
    monkeypatch.setattr(engines, "ASSETS", {"asset.bin": (3, "bad-hash")})
    asset.write_bytes(b"abc")
    assert engines._model_signature(settings.docling_models)
    with pytest.raises(ConversionError):
        engines._verify_models(settings.docling_models)
    asset.write_bytes(b"bad-size")
    with pytest.raises(ConversionError):
        engines._model_signature(settings.docling_models)


def test_availability_cache_and_invalidation(settings, monkeypatch):
    # Model a supported, trusted runtime; platform gating is tested separately.
    monkeypatch.setattr(
        engines, "_paths", lambda config: (config.docling_python, config.docling_models)
    )
    settings.docling_models.mkdir()
    asset = settings.docling_models / "asset.bin"
    asset.write_bytes(b"abc")
    import hashlib

    monkeypatch.setattr(
        engines, "ASSETS", {"asset.bin": (3, hashlib.sha256(b"abc").hexdigest())}
    )
    engines._probe_cache.clear()
    calls = []
    monkeypatch.setattr(
        engines,
        "_invoke",
        lambda *a: calls.append(a)
        or {"ready": True, "version": docling_adapter.VERSION},
    )
    engines.ensure_engine_available("docling", settings)
    engines.ensure_engine_available("docling", settings)
    assert len(calls) == 1
    asset.write_bytes(b"def")
    with pytest.raises(ConversionError):
        engines.ensure_engine_available("docling", settings)
    assert not list(settings.data_dir.glob("engine-*"))


@pytest.mark.parametrize(
    "suffix,data,match",
    [
        (".txt", b"%PDF-hi", "仅支持有效"),
        (".pdf", b"nope", "仅支持有效"),
        (".pdf", b"%PDF-" + b"x" * (10 * 1024**2), "10 MiB"),
    ],
)
def test_admission_rejects_before_parser(settings, monkeypatch, suffix, data, match):
    monkeypatch.setattr(engines, "ensure_engine_available", lambda *a: None)
    monkeypatch.setattr(
        engines, "_invoke", lambda *a: pytest.fail("Parser should not start")
    )
    with pytest.raises(ConversionError, match=match):
        engines.validate_engine_uploads(
            "docling", [{"suffix": suffix, "data": data}], settings
        )


@pytest.mark.parametrize("pages", [0, 3, 20, True, "2", None])
def test_preflight_page_limit_is_authoritative(settings, monkeypatch, pages):
    monkeypatch.setattr(engines, "ensure_engine_available", lambda *a: None)
    monkeypatch.setattr(engines, "_invoke", lambda *a: {"page_count": pages})
    with pytest.raises(ConversionError, match="1 至 2"):
        engines.validate_engine_uploads(
            "docling", [{"suffix": ".pdf", "data": b"%PDF-hi"}], settings
        )
    assert not list(settings.data_dir.glob("engine-*"))


def test_preflight_success_cleans_source(settings, monkeypatch):
    monkeypatch.setattr(engines, "ensure_engine_available", lambda *a: None)

    def invoke(settings, mode, source, directory, cancelled):
        assert source.read_bytes() == b"%PDF-hi"
        assert source.stat().st_mode & 0o777 == 0o600
        assert mode == "preflight"
        return {"page_count": 2}

    monkeypatch.setattr(engines, "_invoke", invoke)
    engines.validate_engine_uploads(
        "docling", [{"suffix": ".pdf", "data": b"%PDF-hi"}], settings
    )
    assert not list(settings.data_dir.glob("engine-*"))


def test_environment_does_not_inherit_secret_or_proxy(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", "synthetic-only")
    monkeypatch.setenv("HTTPS_PROXY", "http://example.invalid")
    env = engines._environment(tmp_path)
    assert "FAKE_API_KEY" not in env and "HTTPS_PROXY" not in env
    assert all(env[key] == "1" for key in docling_adapter.OFFLINE_ENV)
    assert env["HOME"] == str(tmp_path)


@pytest.mark.parametrize(
    "result,match",
    [
        ({"error": "/private/document-content"}, "增强失败"),
        ({"error": "incomplete"}, "拒绝部分结果"),
        ({"error": "no_text"}, "扫描件暂不支持"),
        ([], "增强失败"),
    ],
)
def test_error_boundary(tmp_path, result, match):
    path = tmp_path / "result.json"
    path.write_text(json.dumps(result))
    with pytest.raises(ConversionError, match=match):
        engines._read_result(path)


def test_result_symlink_refused(tmp_path):
    target = tmp_path / "real"
    target.write_text("{}")
    link = tmp_path / "result"
    link.symlink_to(target)
    with pytest.raises(OSError):
        engines._read_result(link)


@pytest.fixture
def fake_child(tmp_path, settings, monkeypatch):
    path = tmp_path / "synthetic-child.py"
    path.write_text(
        "import json,sys,time\nfrom pathlib import Path\nmode,source,output,models=sys.argv[1:]\nif mode=='sleep': time.sleep(30)\nelse: Path(output).write_text(json.dumps({'ready':True}))\n"
    )
    monkeypatch.setattr(engines, "WORKER", path)
    monkeypatch.setattr(
        engines, "_paths", lambda settings: (Path(sys.executable), tmp_path)
    )
    # These children exercise portable timeout/cancel orchestration, not Linux
    # /proc. Dedicated RSS-threshold tests override this value explicitly.
    monkeypatch.setattr(engines, "_rss", lambda pid: 0)
    return path


def test_child_timeout_reaped(settings, fake_child, monkeypatch):
    monkeypatch.setattr(engines, "PREFLIGHT_SECONDS", 0.05)
    with pytest.raises(ConversionError, match="超时"):
        engines._invoke(
            settings, "sleep", settings.data_dir, settings.data_dir, lambda: False
        )


def test_cancel_before_spawn(settings, fake_child, monkeypatch):
    monkeypatch.setattr(
        engines.subprocess, "Popen", lambda *a, **k: pytest.fail("Should not spawn")
    )
    with pytest.raises(ConversionError, match="已取消"):
        engines._invoke(
            settings, "probe", settings.data_dir, settings.data_dir, lambda: True
        )


def test_cancel_running_child(settings, fake_child):
    checks = []

    def cancelled():
        checks.append(True)
        return len(checks) >= 3

    with pytest.raises(ConversionError, match="已取消"):
        engines._invoke(
            settings, "sleep", settings.data_dir, settings.data_dir, cancelled
        )


def test_rss_threshold_enforced(settings, fake_child, monkeypatch):
    monkeypatch.setattr(engines, "_rss", lambda pid: engines.RSS_BYTES + 1)
    with pytest.raises(ConversionError, match="资源限制"):
        engines._invoke(
            settings, "sleep", settings.data_dir, settings.data_dir, lambda: False
        )


def test_scratch_threshold_enforced(settings, fake_child, monkeypatch):
    monkeypatch.setattr(engines, "_scratch_bytes", lambda path: engines.TEMP_BYTES + 1)
    with pytest.raises(ConversionError, match="资源限制"):
        engines._invoke(
            settings, "sleep", settings.data_dir, settings.data_dir, lambda: False
        )


def test_free_disk_guard(settings, fake_child, monkeypatch):
    monkeypatch.setattr(
        engines.shutil, "disk_usage", lambda path: SimpleNamespace(free=0)
    )
    with pytest.raises(ConversionError, match="存储空间不足"):
        engines._invoke(
            settings, "probe", settings.data_dir, settings.data_dir, lambda: False
        )


def test_kernel_network_denial_in_disposable_process(tmp_path):
    if sys.platform != "linux":
        pytest.skip("Linux optional engine only")
    script = f"import runpy,resource,json; w=runpy.run_path({str(Path(docling_worker.__file__))!r}); w['restrict']('probe'); print(json.dumps({{'cpu':resource.getrlimit(resource.RLIMIT_CPU)[0], 'as':resource.getrlimit(resource.RLIMIT_AS)[0]}}))"
    env = engines._environment(tmp_path)
    result = subprocess.run(
        [sys.executable, "-I", "-c", script], env=env, capture_output=True, timeout=8
    )
    assert result.returncode == 0, result.stderr.decode()
    assert json.loads(result.stdout) == {"cpu": 5, "as": 1024**3}
    # Exercise process/thread distinctions under the exact deployed filter.
    script = f"""import runpy,os,threading,socket,json
w=runpy.run_path({str(Path(docling_worker.__file__))!r}); w['restrict']('probe')
results={{}}
try:
    child=os.fork()
    if child == 0: os._exit(0)
    os.waitpid(child, 0)
    results['fork']=0
except OSError as exc: results['fork']=exc.errno
try:
    os.setsid(); results['setsid']=0
except OSError as exc: results['setsid']=exc.errno
def check_thread():
    results['thread']=True
    try: socket.socket(); results['socket']=0
    except OSError as exc: results['socket']=exc.errno
t=threading.Thread(target=check_thread); t.start(); t.join()
print(json.dumps(results))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script], env=env, capture_output=True, timeout=8
    )
    assert result.returncode == 0, result.stderr.decode()
    assert json.loads(result.stdout) == {
        "fork": 1,
        "setsid": 1,
        "thread": True,
        "socket": 1,
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("page_count", 3),
        ("page_count", True),
        ("duration_seconds", float("inf")),
        ("duration_seconds", -1),
        ("duration_seconds", True),
        ("version", "wrong"),
        ("profile", "unknown"),
        ("ocr", True),
    ],
)
def test_result_metadata_refused(settings, monkeypatch, key, value):
    path = settings.data_dir / "source.pdf"
    path.write_bytes(b"%PDF-synthetic")
    metadata = {
        "engine": "docling",
        "version": docling_adapter.VERSION,
        "profile": docling_adapter.PROFILE,
        "page_count": 2,
        "duration_seconds": 1.2,
        "ocr": False,
    }
    metadata[key] = value
    monkeypatch.setattr(engines, "ensure_engine_available", lambda *a: None)
    monkeypatch.setattr(
        engines,
        "_invoke",
        lambda *a: {
            "markdown": "# Fine",
            "html": "<h1>Fine</h1>",
            "metadata": metadata,
        },
    )
    with pytest.raises(ConversionError):
        engines.run_docling_conversion(path, settings, lambda: False)
    assert not list(settings.data_dir.glob("docling-*"))


def test_child_preview_and_metadata_whitelisted(settings, monkeypatch):
    path = settings.data_dir / "source.pdf"
    path.write_bytes(b"%PDF-synthetic")
    metadata = {
        "engine": "docling",
        "version": docling_adapter.VERSION,
        "profile": docling_adapter.PROFILE,
        "page_count": 2,
        "duration_seconds": 1.2,
        "ocr": False,
        "private_path": "/secret",
        "warnings": ["secret"],
    }
    monkeypatch.setattr(engines, "ensure_engine_available", lambda *a: None)
    monkeypatch.setattr(
        engines,
        "_invoke",
        lambda *a: {
            "markdown": "# Fine\n<script>alert(1)</script>\n![img](https://example.invalid)",
            "html": "<h1>Fine</h1>",
            "metadata": metadata,
        },
    )
    markdown, html, safe = engines.run_docling_conversion(path, settings, lambda: False)
    assert "<script>" not in html and "<img" not in html
    assert "private_path" not in safe and safe["warnings"] == []


def test_preflight_busy_rejects_without_staging(settings, monkeypatch):
    monkeypatch.setattr(engines, "ensure_engine_available", lambda *a: None)
    engines._preflight_slot.acquire()
    try:
        with pytest.raises(ConversionError, match="繁忙"):
            engines.validate_engine_uploads(
                "docling", [{"suffix": ".pdf", "data": b"%PDF-hi"}], settings
            )
        assert not list(settings.data_dir.glob("engine-*"))
    finally:
        engines._preflight_slot.release()


def test_parent_renderer_is_sanitized_and_bounded(settings, monkeypatch):
    path = settings.data_dir / "source.pdf"
    path.write_bytes(b"%PDF-test")
    fake = SimpleNamespace(
        runtime_version=lambda: "2.133.0",
        MAX_FILE_BYTES=10 * 1024**2,
        MAX_PAGES=2,
        AdapterError=docling_adapter.AdapterError,
        convert=lambda *a: (
            "<script>bad()</script>\n![img](https://example.invalid)",
            {},
        ),
    )
    original_load = docling_worker.load_module
    monkeypatch.setattr(
        docling_worker,
        "load_module",
        lambda name: fake if name == "docling_adapter" else original_load(name),
    )
    monkeypatch.setattr(docling_worker, "page_count", lambda *a: 1)
    result = docling_worker.run("convert", path, settings.docling_models)
    assert "html" not in result
    _, preview = validated_preview(result["markdown"])
    assert "<script>" not in preview and "<img" not in preview
    fake.convert = lambda *a: ("x" * (5 * 1024**2), {})
    with pytest.raises(ConversionError, match="2 MiB"):
        result = docling_worker.run("convert", path, settings.docling_models)
        validated_preview(result["markdown"])


@pytest.fixture
def fake_docling(monkeypatch):
    from types import ModuleType

    recorded = {}
    document = SimpleNamespace(
        export_to_markdown=lambda **kwargs: recorded.update(export=kwargs)
        or "# Synthetic"
    )
    result = SimpleNamespace(
        status="SUCCESS",
        input=SimpleNamespace(page_count=2),
        pages=[1, 2],
        document=document,
    )

    def options(**kwargs):
        recorded["options"] = kwargs
        return kwargs

    class Converter:
        def __init__(self, **kwargs):
            recorded["converter"] = kwargs

        def convert(self, path, **kwargs):
            recorded["convert"] = kwargs
            return result

    modules = {
        "docling.datamodel.accelerator_options": {
            "AcceleratorDevice": SimpleNamespace(CPU="cpu"),
            "AcceleratorOptions": lambda **kwargs: kwargs,
        },
        "docling.datamodel.base_models": {
            "ConversionStatus": SimpleNamespace(SUCCESS="SUCCESS"),
            "InputFormat": SimpleNamespace(PDF="pdf"),
        },
        "docling.datamodel.pipeline_options": {"PdfPipelineOptions": options},
        "docling.document_converter": {
            "DocumentConverter": Converter,
            "PdfFormatOption": lambda **kwargs: kwargs,
        },
        "docling_core.types.doc": {
            "ContentLayer": SimpleNamespace(BODY="body", FURNITURE="furniture"),
            "ImageRefMode": SimpleNamespace(PLACEHOLDER="placeholder"),
        },
        "torch": {
            "set_num_threads": lambda n: recorded.update(threads=n),
            "set_num_interop_threads": lambda n: recorded.update(interop=n),
        },
    }
    for name, values in modules.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(
        docling_adapter, "runtime_version", lambda: docling_adapter.VERSION
    )
    for key, value in docling_adapter.OFFLINE_ENV.items():
        monkeypatch.setenv(key, value)
    return recorded, result


def test_adapter_explicit_offline_profile(fake_docling, tmp_path):
    recorded, _ = fake_docling
    markdown, metadata = docling_adapter.convert(
        tmp_path / "source.pdf", tmp_path / "models"
    )
    options = recorded["options"]
    for key in (
        "enable_remote_services",
        "allow_external_plugins",
        "do_ocr",
        "do_code_enrichment",
        "do_formula_enrichment",
        "do_picture_classification",
        "do_picture_description",
        "do_chart_extraction",
        "generate_page_images",
        "generate_picture_images",
        "generate_table_images",
        "generate_parsed_pages",
    ):
        assert options[key] is False
    assert options["do_table_structure"] is True
    assert options["document_timeout"] == 30
    assert options["accelerator_options"] == {"num_threads": 2, "device": "cpu"}
    assert recorded["convert"] == {
        "raises_on_error": False,
        "max_num_pages": 2,
        "max_file_size": 10 * 1024**2,
    }
    assert recorded["export"] == {
        "image_mode": "placeholder",
        "included_content_layers": {"body", "furniture"},
    }
    assert metadata["page_count"] == 2 and metadata["ocr"] is False
    assert markdown == "# Synthetic"


@pytest.mark.parametrize("status", ["PARTIAL_SUCCESS", "FAILURE", "SKIPPED", None])
def test_adapter_never_accepts_partial(fake_docling, tmp_path, status):
    _, result = fake_docling
    result.status = status
    with pytest.raises(docling_adapter.AdapterError, match="incomplete"):
        docling_adapter.convert(tmp_path / "source.pdf", tmp_path / "models")


@pytest.mark.parametrize("pages,processed", [(3, 3), (0, 0), (2, 1)])
def test_adapter_requires_all_pages(fake_docling, tmp_path, pages, processed):
    _, result = fake_docling
    result.input.page_count = pages
    result.pages = [1] * processed
    with pytest.raises(docling_adapter.AdapterError, match="incomplete"):
        docling_adapter.convert(tmp_path / "source.pdf", tmp_path / "models")


@pytest.mark.parametrize(
    "output,code",
    [
        ("<!-- image -->", "no_text"),
        (" ", "no_text"),
        ("x" * (2 * 1024**2 + 1), "output_limit"),
    ],
)
def test_adapter_output_limits(fake_docling, tmp_path, output, code):
    _, result = fake_docling
    result.document.export_to_markdown = lambda **kwargs: output
    with pytest.raises(docling_adapter.AdapterError, match=code):
        docling_adapter.convert(tmp_path / "source.pdf", tmp_path / "models")


def test_teardown_waits_for_confirmed_reap_after_kill(monkeypatch):
    signals, waits = [], []
    monkeypatch.setattr(
        engines.os, "killpg", lambda pid, sig: signals.append((pid, sig))
    )

    def wait(**kwargs):
        waits.append(kwargs)
        if len(waits) == 1:
            raise subprocess.TimeoutExpired("synthetic", 1)
        return -9

    process = SimpleNamespace(pid=12345, wait=wait)
    engines._terminate(process)
    assert [int(item[1]) for item in signals if item[1]] == [15, 9]
    assert waits == [{"timeout": 0.5}, {}]


def test_teardown_signals_group_after_leader_exit(monkeypatch):
    signals = []
    monkeypatch.setattr(
        engines.os, "killpg", lambda pid, sig: signals.append((pid, sig))
    )
    process = SimpleNamespace(pid=12345, returncode=0, wait=lambda **kwargs: 0)
    engines._terminate(process)
    assert len([item for item in signals if item[1]]) == 2


def test_production_missing_rss_tree_visibility_fails_closed_and_reaps(
    settings, monkeypatch
):
    from markitdown_web import sandbox

    settings.deployment_mode = "production"
    monkeypatch.setattr(
        engines, "_paths", lambda config: (Path(sys.executable), config.docling_models)
    )
    monkeypatch.setattr(
        sandbox, "command", lambda *args, **kwargs: (["synthetic-never-executed"], {})
    )
    process = SimpleNamespace(pid=987654321, poll=lambda: None, returncode=None)
    monkeypatch.setattr(engines.subprocess, "Popen", lambda *args, **kwargs: process)
    reaped = []
    monkeypatch.setattr(engines, "_terminate", lambda child: reaped.append(child))
    monkeypatch.setattr(
        engines,
        "_rss",
        lambda pid: pytest.fail("Production used local process-only measurement"),
    )

    def sample(pid, *, require_tree=False):
        assert require_tree is True
        raise sandbox.ResourceMonitoringUnavailable("Synthetic unavailable children")

    monkeypatch.setattr(sandbox, "process_tree_rss", sample)
    with pytest.raises(ConversionError, match="资源监测不可用") as error:
        engines._invoke(
            settings,
            "convert",
            settings.data_dir / "source.pdf",
            settings.data_dir,
            lambda: False,
        )
    assert "children" not in str(error.value)
    assert reaped == [process]


def test_local_config_labels_rss_process_scope(settings, monkeypatch):
    monkeypatch.setattr(engines, "ensure_engine_available", lambda *args: None)
    assert (
        engines.engine_config(settings)[1]["rss_measurement_scope"]
        == "direct-parser-process"
    )


@pytest.mark.parametrize("exited", [False, True])
def test_production_missing_root_is_accepted_only_after_supervisor_exit(
    settings, monkeypatch, exited
):
    from markitdown_web import sandbox

    settings.deployment_mode = "production"
    monkeypatch.setattr(
        engines, "_paths", lambda config: (Path(sys.executable), config.docling_models)
    )
    monkeypatch.setattr(
        sandbox, "command", lambda *args, **kwargs: (["synthetic-never-executed"], {})
    )
    polls = []
    process = SimpleNamespace(pid=987654321, returncode=None)

    def poll():
        polls.append(True)
        if len(polls) > 1 and exited:
            process.returncode = 0
        return process.returncode

    process.poll = poll

    def launch(*args, **kwargs):
        (settings.data_dir / "engine-result.json").write_text(
            json.dumps({"error": "page_limit"})
        )
        return process

    monkeypatch.setattr(engines.subprocess, "Popen", launch)
    reaped, results = [], []
    monkeypatch.setattr(engines, "_terminate", lambda child: reaped.append(child))
    read_result = engines._read_result

    def record_result(path):
        results.append(path)
        return read_result(path)

    monkeypatch.setattr(engines, "_read_result", record_result)

    def missing(pid, *, require_tree=False):
        assert require_tree is True
        raise FileNotFoundError("Synthetic confirmed-missing root")

    monkeypatch.setattr(sandbox, "process_tree_rss", missing)
    with pytest.raises(ConversionError) as error:
        engines._invoke(
            settings,
            "preflight",
            settings.data_dir / "source.pdf",
            settings.data_dir,
            lambda: False,
        )
    assert (
        str(error.value)
        == engines.ERRORS["page_limit" if exited else "conversion_failed"]
    )
    assert len(polls) == 2 and len(results) == int(exited)
    assert reaped == [process]
