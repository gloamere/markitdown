"""Portable path/command contracts, never live production-isolation evidence.

The companion CI script alone runs the real parser and preview with pinned
models. These fixtures contain non-runnable placeholder executables/assets and
only model Linux for construction checks; no bwrap or Docling process starts.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from markitdown_web import engines, sandbox
from markitdown_web.conversion import SAFE_ERRORS, ConversionError
from markitdown_web.docling_adapter import ASSETS, OFFLINE_ENV

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/ci/verify_production_docling.py"
SPEC = importlib.util.spec_from_file_location("verify_production_docling", SCRIPT)
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)
IMAGE = "sha256:" + "a" * 64


@pytest.fixture
def production_settings(tmp_path, monkeypatch):
    # Canonicalize trusted macOS /var ancestors, without weakening any guard.
    root = tmp_path.resolve()
    monkeypatch.setattr(sandbox, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(engines, "sys", SimpleNamespace(platform="linux"))
    image = root / "image"
    image.mkdir()
    for name in ("input", "output", "code", "models", "tmp", "proc", "dev"):
        (image / name).mkdir()
    (image / "markitdown-runtime.json").write_text(
        json.dumps({"profile": sandbox.PROFILE, "image_id": IMAGE})
    )
    for name in ("usr/bin/python3", "opt/docling/bin/python"):
        interpreter = image / name
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text("command fixture only; never run")
        interpreter.chmod(0o700)
    launcher = root / "bwrap-fixture"
    launcher.write_text("command fixture only; never run")
    launcher.chmod(0o700)
    data = root / "private"
    data.mkdir(mode=0o700)
    models = root / "approved-models"
    for relative in ASSETS:
        asset = models / relative
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b"contract fixture, not actual model data")
    return replace(
        smoke.production_settings(data, image, models),
        sandbox_bwrap=launcher,
    )


def command(settings, mode="convert"):
    source = None
    if mode != "probe":
        source = settings.data_dir / "synthetic.pdf"
        source.write_bytes(smoke.synthetic_pdf(2))
    output = settings.data_dir / "engine-result.json"
    output.touch()
    argv, env = sandbox.command(
        settings,
        engine="docling",
        mode=mode,
        source=source,
        output=output,
        models=settings.docling_models,
    )
    return argv, env, source, output


def mounts(argv, option):
    return [
        tuple(argv[index + 1 : index + 3])
        for index, arg in enumerate(argv)
        if arg == option
    ]


def test_smoke_uses_real_production_settings(production_settings):
    settings = production_settings
    assert settings.deployment_mode == "production"
    assert settings.public_origin == "https://synthetic.invalid"
    assert settings.cookie_secure and settings.docling_enabled
    assert settings.docling_python is None
    assert settings.start_workers is False
    assert settings.sandbox_python == "/usr/bin/python3"
    assert settings.sandbox_docling_python == "/opt/docling/bin/python"
    assert smoke.verify_runtime(settings, IMAGE) == {
        "runtime_image_id": IMAGE,
        "sandbox_profile": sandbox.PROFILE,
        "production_boundary": True,
        "rss_measurement_scope": "complete-wrapper-tree-required",
    }


def test_production_path_ignores_host_docling_interpreter(production_settings):
    settings = replace(production_settings, docling_python=Path(sys.executable))
    python, models = engines._paths(settings)
    assert python == settings.sandbox_runtime_root / "opt/docling/bin/python"
    assert models == settings.docling_models


def test_missing_image_docling_never_falls_back_to_host(production_settings):
    settings = replace(production_settings, docling_python=Path(sys.executable))
    (settings.sandbox_runtime_root / "opt/docling/bin/python").unlink()
    with pytest.raises(ConversionError, match="尚未就绪"):
        engines._paths(settings)


@pytest.mark.parametrize("engine", ["docling", "preview"])
def test_runtime_identity_matches_both_interpreters(production_settings, engine):
    root, _, python = sandbox.runtime_paths(production_settings, engine)
    assert root == production_settings.sandbox_runtime_root
    assert python == (
        "/opt/docling/bin/python" if engine == "docling" else "/usr/bin/python3"
    )
    assert (
        sandbox.execution_snapshot(production_settings, engine)["runtime_image_id"]
        == IMAGE
    )
    with pytest.raises(ValueError, match="identity-mismatch"):
        smoke.verify_runtime(production_settings, "sha256:" + "b" * 64)


def test_preview_interpreter_is_required_before_live_probe(production_settings):
    (production_settings.sandbox_runtime_root / "usr/bin/python3").unlink()
    with pytest.raises(sandbox.SandboxUnavailable):
        smoke.verify_runtime(production_settings, IMAGE)


def test_docling_interpreter_cannot_escape_runtime_image(production_settings):
    interpreter = production_settings.sandbox_runtime_root / "opt/docling/bin/python"
    interpreter.unlink()
    interpreter.symlink_to(Path(sys.executable).resolve())
    with pytest.raises(ConversionError, match="尚未就绪"):
        engines._paths(production_settings)


def test_in_image_interpreter_symlink_keeps_image_identity(production_settings):
    interpreter = production_settings.sandbox_runtime_root / "opt/docling/bin/python"
    real = interpreter.with_name("python3.12")
    interpreter.rename(real)
    interpreter.symlink_to("python3.12")
    assert engines._paths(production_settings)[0] == interpreter
    assert smoke.verify_runtime(production_settings, IMAGE)["runtime_image_id"] == IMAGE


@pytest.mark.parametrize("mode", ["probe", "preflight", "convert"])
def test_docling_commands_bind_only_five_approved_models(production_settings, mode):
    settings = production_settings
    (settings.docling_models / "unexpected-private-file").write_text("do not mount")
    argv, env, source, output = command(settings, mode)
    readonly = mounts(argv, "--ro-bind")
    model_mounts = [pair for pair in readonly if pair[1].startswith("/models/")]
    assert model_mounts == [
        (str(settings.docling_models / relative), "/models/" + relative)
        for relative in ASSETS
    ]
    assert len(model_mounts) == 5
    assert (str(settings.sandbox_runtime_root), "/") in readonly
    assert len(readonly) == 1 + len(sandbox.CODE_FILES) + 5 + (source is not None)
    assert mounts(argv, "--bind") == [(str(output), "/output/result.json")]
    assert str(settings.docling_models) not in argv
    assert str(settings.data_dir) not in argv
    assert str(settings.docling_models / "unexpected-private-file") not in argv
    assert str(Path.home()) not in argv
    assert argv[argv.index("--") + 1 :] == [
        "/opt/docling/bin/python",
        "-I",
        "-B",
        "/code/markitdown_web/docling_worker.py",
        mode,
        "/input/unused" if mode == "probe" else "/input/source.pdf",
        "/output/result.json",
        "/models",
    ]
    if source is not None:
        assert (str(source), "/input/source.pdf") in readonly
    # bwrap receives a minimal supervisor environment; --clearenv/--setenv
    # construct the separate in-namespace worker environment.
    worker_env = dict(mounts(argv, "--setenv"))
    assert all(worker_env[key] == value for key, value in OFFLINE_ENV.items())
    assert worker_env["MARKITDOWN_WORKER_MODE"] == mode
    assert worker_env["MARKITDOWN_SANDBOX_PROFILE"] == sandbox.PROFILE
    assert worker_env["HF_HOME"] == "/tmp/hf"
    assert worker_env["TORCH_HOME"] == "/tmp/torch"
    assert worker_env["OMP_NUM_THREADS"] == "2"
    assert "MARKITDOWN_PARENT_PID" not in worker_env
    assert "HTTPS_PROXY" not in worker_env and "OPENAI_API_KEY" not in worker_env
    assert "HTTPS_PROXY" not in env and "OPENAI_API_KEY" not in env


def test_docling_command_preserves_mandatory_isolation(production_settings):
    argv, _, _, _ = command(production_settings)
    for flag in (
        "--unshare-all",
        "--unshare-user",
        "--unshare-cgroup",
        "--die-with-parent",
        "--disable-userns",
        "--clearenv",
    ):
        assert flag in argv
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--size") + 1] == str(sandbox.TMP_BYTES)
    assert "--share-net" not in argv and "--unshare-user-try" not in argv
    assert "--unshare-cgroup-try" not in argv
    assert [argv[i + 1] for i, value in enumerate(argv) if value == "--remount-ro"] == [
        "/input",
        "/output",
        "/code",
        "/models",
        "/proc",
        "/dev",
    ]


def test_independent_preview_uses_same_image_without_models(production_settings):
    settings = production_settings
    source = settings.data_dir / "source.md"
    source.write_text("synthetic Markdown")
    output = settings.data_dir / "preview-result.json"
    output.touch()
    argv, env = sandbox.command(
        settings,
        engine="preview",
        mode="preview",
        source=source,
        output=output,
        suffix=".md",
    )
    readonly = mounts(argv, "--ro-bind")
    assert (str(settings.sandbox_runtime_root), "/") in readonly
    assert (str(source), "/input/source.md") in readonly
    assert len(readonly) == 2 + len(sandbox.CODE_FILES)
    assert not any(target.startswith("/models/") for _, target in readonly)
    assert argv[argv.index("--") + 1] == "/usr/bin/python3"
    assert "markitdown_web.preview import worker_main" in argv[argv.index("-c") + 1]
    assert mounts(argv, "--bind") == [(str(output), "/output/result.json")]
    worker_env = dict(mounts(argv, "--setenv"))
    assert worker_env["MARKITDOWN_WORKER_MODE"] == "preview"
    assert worker_env["MARKITDOWN_SANDBOX_PROFILE"] == sandbox.PROFILE
    assert worker_env["OMP_NUM_THREADS"] == "1"
    assert "HTTPS_PROXY" not in env and "OPENAI_API_KEY" not in env


@pytest.mark.parametrize("relative", list(ASSETS))
def test_missing_approved_model_refuses_command(production_settings, relative):
    (production_settings.docling_models / relative).unlink()
    with pytest.raises((OSError, sandbox.SandboxUnavailable)):
        command(production_settings)


def test_model_root_symlink_rejected_before_command(production_settings):
    link = production_settings.data_dir.parent / "model-link"
    link.symlink_to(production_settings.docling_models, target_is_directory=True)
    settings = replace(production_settings, docling_models=link)
    with pytest.raises(ConversionError, match="尚未就绪"):
        engines._paths(settings)
    with pytest.raises(sandbox.SandboxUnavailable):
        command(settings)


def test_nested_model_symlink_rejected_by_command(production_settings):
    asset = production_settings.docling_models / next(iter(ASSETS))
    outside = production_settings.data_dir / "outside-model"
    asset.rename(outside)
    asset.symlink_to(outside)
    with pytest.raises(sandbox.SandboxUnavailable):
        command(production_settings)


def test_model_subdirectory_symlink_rejected_by_command(production_settings):
    directory = (
        production_settings.docling_models / "docling-project--docling-layout-heron"
    )
    outside = production_settings.data_dir / "outside-model-directory"
    directory.rename(outside)
    directory.symlink_to(outside, target_is_directory=True)
    with pytest.raises(sandbox.SandboxUnavailable):
        command(production_settings)


@pytest.mark.parametrize(
    "target", ["models", "input", "output", "code", "tmp", "proc", "dev"]
)
def test_docling_keeps_production_mount_target_guards(production_settings, target):
    (production_settings.sandbox_runtime_root / target / "unexpected-state").touch()
    with pytest.raises(ConversionError, match="尚未就绪"):
        engines._paths(production_settings)


def test_existing_model_manifest_is_the_same_five_pinned_assets():
    metadata = smoke.model_manifest_metadata()
    assert metadata["model_files"] == 5
    assert metadata["model_bytes"] == 384428156
    assert re.fullmatch(r"[a-f0-9]{64}", metadata["model_manifest_sha256"])


@pytest.mark.parametrize("pages", [2, 3])
def test_synthetic_pdf_is_deterministic_with_valid_xref_and_page_tree(pages):
    data = smoke.synthetic_pdf(pages)
    assert data == smoke.synthetic_pdf(pages)
    assert len(data) < 4096
    assert data.startswith(b"%PDF-1.4\n") and data.endswith(b"%%EOF\n")
    assert data.count(b"/Type /Page ") == pages
    assert f"/Count {pages} ".encode("ascii") in data
    startxref = int(data.rsplit(b"startxref\n", 1)[1].splitlines()[0])
    entries = data[startxref:].splitlines()
    assert entries[0] == b"xref"
    count = int(entries[1].split()[1])
    assert count == 4 + pages * 2
    for number, entry in enumerate(entries[3 : count + 2], 1):
        offset = int(entry.split()[0])
        assert data[offset:].startswith(f"{number} 0 obj\n".encode("ascii"))
    streams = re.findall(rb"/Length (\d+) >>\nstream\n(.*?)\nendstream", data, re.S)
    assert len(streams) == pages
    assert all(int(length) == len(stream) for length, stream in streams)
    for number in range(1, pages + 1):
        assert f"Synthetic production page {number}".encode("ascii") in data


@pytest.mark.parametrize("pages", [0, 1, 4, True, "2", None])
def test_synthetic_generator_stays_bounded(pages):
    with pytest.raises(ValueError, match="unsupported-synthetic-page-count"):
        smoke.synthetic_pdf(pages)


def test_unavailable_runtime_has_content_free_failed_report(tmp_path, capsys):
    output = tmp_path / "report.json"
    secret = "private-document-name-never-log"
    status = smoke.main(
        [
            "--runtime-root",
            str(tmp_path / secret),
            "--models",
            str(tmp_path / "private-model-path"),
            "--image-id",
            IMAGE,
            "--output",
            str(output),
        ]
    )
    assert status == 1
    report = json.loads(output.read_text())
    assert report["success"] is False
    assert report["failed_check"] == "production_runtime_paths"
    assert report["failure"]["exception_type"] in {
        "FileNotFoundError",
        "SandboxUnavailable",
    }
    assert "error_code" not in report["failure"]
    assert report["passed"] == 0 and report["total"] == len(smoke.CHECKS)
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == report
    assert secret not in captured.out and "private-model-path" not in captured.out
    assert str(tmp_path) not in captured.out and "Traceback" not in captured.out


@pytest.mark.parametrize(
    "image", ["not-an-image", "sha256:" + "g" * 64, "/private/input"]
)
def test_invalid_image_identity_is_not_echoed(tmp_path, image):
    report = smoke.run_smoke(tmp_path / "missing", tmp_path / "models", image)
    assert report["success"] is False and report["passed"] == 0
    assert report["failure"] == {"exception_type": "ValueError"}
    assert image not in json.dumps(report)


@pytest.mark.parametrize(
    "code,message",
    list(engines.ERRORS.items())
    + list(SAFE_ERRORS.items())
    + list(smoke.SUPERVISOR_ERRORS.items()),
)
def test_only_known_error_messages_become_canonical_codes(code, message):
    assert smoke.failure_metadata(ConversionError(message)) == {
        "exception_type": "ConversionError",
        "error_code": code,
    }


@pytest.mark.parametrize("error", [ValueError, OSError, ConversionError])
def test_unknown_failure_messages_never_enter_diagnostics(error):
    secret = "/private/source.pdf: private document contents"
    assert smoke.failure_metadata(error(secret)) == {"exception_type": error.__name__}
    assert secret not in json.dumps(smoke.failure_metadata(error(secret)))


def test_three_page_harness_accepts_only_expected_rejection(monkeypatch):
    # Harness-only branch tests replace the API at its boundary. The live CI
    # script itself never patches admission, parser, security or monitoring.
    settings = object()
    payload = smoke.synthetic_pdf(3)
    calls = []

    def reject(engine, uploads, actual_settings):
        calls.append((engine, uploads, actual_settings))
        raise ConversionError(engines.ERRORS["page_limit"])

    monkeypatch.setattr(engines, "validate_engine_uploads", reject)
    assert smoke.require_three_page_rejection(settings, payload) is None
    assert calls == [("docling", [{"suffix": ".pdf", "data": payload}], settings)]


@pytest.mark.parametrize(
    "code,message",
    [
        (code, message)
        for code, message in engines.ERRORS.items()
        if code != "page_limit"
    ]
    + list(smoke.SUPERVISOR_ERRORS.items()),
)
def test_three_page_harness_preserves_unexpected_refusal(monkeypatch, code, message):
    refusal = ConversionError(message)

    def reject(*args):
        raise refusal

    monkeypatch.setattr(engines, "validate_engine_uploads", reject)
    with pytest.raises(ConversionError) as caught:
        smoke.require_three_page_rejection(object(), smoke.synthetic_pdf(3))
    assert caught.value is refusal
    assert smoke.failure_metadata(caught.value) == {
        "exception_type": "ConversionError",
        "error_code": code,
    }


def test_three_page_harness_distinguishes_false_acceptance(monkeypatch):
    monkeypatch.setattr(engines, "validate_engine_uploads", lambda *args: None)
    with pytest.raises(smoke.ThreePageAccepted) as caught:
        smoke.require_three_page_rejection(object(), smoke.synthetic_pdf(3))
    assert isinstance(caught.value, AssertionError)
    assert smoke.failure_metadata(caught.value) == {
        "exception_type": "ThreePageAccepted"
    }
