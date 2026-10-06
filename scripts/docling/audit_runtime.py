#!/usr/bin/env python3
"""Verify the installed CPU-only runtime, model integrity, and pinned provenance."""
from __future__ import annotations

import os

os.environ["ORT_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

import argparse
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from importlib.metadata import distributions
from pathlib import Path

import tomllib
from download_models import sha256, verify
from resource_guard import check

HERE = Path(__file__).resolve().parent


def audit(root: Path) -> dict:
    if Path(sys.prefix).resolve() != (root / ".venv").resolve():
        raise RuntimeError(
            "Run this audit with the isolated runtime's .venv/bin/python"
        )
    with (HERE / "uv.lock").open("rb") as source:
        lock = tomllib.load(source)
    expected = {
        p["name"]: p["version"] for p in lock["package"] if "virtual" not in p["source"]
    }
    installed = {
        dist.metadata["Name"].lower().replace("_", "-"): dist.version
        for dist in distributions()
    }
    if installed != expected:
        raise RuntimeError(
            f"Installed packages differ from uv.lock: expected={expected}; installed={installed}"
        )
    forbidden_names = {
        "docling",
        "easyocr",
        "rapidocr",
        "paddleocr",
        "tesserocr",
        "pytesseract",
        "onnxruntime",
        "onnxruntime-gpu",
        "open-clip-torch",
        "qwen-vl-utils",
        "mlx-vlm",
        "openai-whisper",
    }
    forbidden = sorted(
        name
        for name in installed
        if name in forbidden_names or name.startswith(("nvidia-", "cuda-"))
    )
    if forbidden:
        raise RuntimeError(f"Disallowed distributions installed: {forbidden}")
    for package in lock["package"]:
        if "virtual" in package["source"]:
            continue
        registry = (
            "https://download.pytorch.org/whl/cpu"
            if package["name"] in {"torch", "torchvision"}
            else "https://pypi.org/simple"
        )
        if package["source"] != {"registry": registry}:
            raise RuntimeError(f"Unexpected package source: {package['name']}")
    import cv2
    import torch
    import torchvision
    from docling.datamodel.pipeline_options import PdfPipelineOptions  # noqa: F401
    from docling.document_converter import DocumentConverter  # noqa: F401

    if (
        torch.version.cuda is not None
        or torch.cuda.is_available()
        or not torch.__version__.endswith("+cpu")
    ):
        raise RuntimeError("Torch is not the requested CPU-only build")
    if not torchvision.__version__.endswith("+cpu"):
        raise RuntimeError("Torchvision is not a CPU build")
    models_root = root / "models"
    model_manifest = json.loads((HERE / "models.json").read_text())
    models = verify(models_root, model_manifest)
    for path in [models_root, *models_root.rglob("*")]:
        if path.is_symlink() or path.stat().st_mode & 0o222:
            raise RuntimeError(f"Model tree must be read-only with no symlinks: {path}")
    sources = json.loads((HERE / "provenance" / "sources.json").read_text())
    for item in sources:
        for directory in [HERE / "provenance", root / "provenance"]:
            path = directory / item["path"]
            if path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
                raise RuntimeError(f"Provenance integrity check failed: {path}")
    resources = check(root)
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "executable": sys.executable,
        "installed_distribution_count": len(installed),
        "installed": dict(sorted(installed.items())),
        "torch_cuda_version": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
        "opencv_import_version": cv2.__version__,
        "ort_disable_telemetry": os.environ["ORT_DISABLE_TELEMETRY"],
        "models": models,
        "models_total_bytes": sum(item["bytes"] for item in models),
        "model_tree_read_only": True,
        "provenance_verified": sources,
        "uv_lock_sha256": hashlib.sha256((HERE / "uv.lock").read_bytes()).hexdigest(),
        "resource_limits": {"max_runtime_gib": 8, "min_free_gib": 10},
        **resources,
    }
    (root / "installed-versions.txt").write_text(
        "".join(f"{name}=={version}\n" for name, version in sorted(installed.items()))
    )
    (root / "runtime-audit.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.runtime.resolve()), indent=2))
