"""CPU-only, offline PDF adapter. Imported inside the disposable worker only.

No OCR, remote services, plugins, VLM, or automatic model provisioning.
"""
from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path
from typing import Any

VERSION = "2.133.0"
PROFILE = "pdf-layout-local-v3"
MAX_PAGES = 2
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_MARKDOWN_BYTES = 2 * 1024 * 1024
PIPELINE_SECONDS = 30
OFFLINE_ENV = {
    key: "1"
    for key in (
        "HF_HUB_OFFLINE",
        "HF_DATASETS_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "HF_HUB_DISABLE_TELEMETRY",
        "ORT_DISABLE_TELEMETRY",
        "DO_NOT_TRACK",
    )
}
# Immutable model revisions and hashes are also retained by scripts/docling.
ASSETS = {
    "docling-project--docling-layout-heron/config.json": (
        3268,
        "fdea30805ce2f5666b147fca941dcdd27ad468e27d6ed21902207d3da056a97d",
    ),
    "docling-project--docling-layout-heron/preprocessor_config.json": (
        444,
        "cd38cd59999e7a95d68e487fbe5132df3d4e5c32a0836add57e6126ba0c4eaf1",
    ),
    "docling-project--docling-layout-heron/model.safetensors": (
        171658996,
        "00333a43451945aaf89db8ca9c0a17e75d1537c17db60fdb91aa95f4c7929e0c",
    ),
    "docling-project--docling-models/model_artifacts/tableformer/accurate/tm_config.json": (
        7060,
        "984e122ceb8ccf84d84c9d2882f6f2302a44b4f1e577babd6289892c36f3cffd",
    ),
    "docling-project--docling-models/model_artifacts/tableformer/accurate/tableformer_accurate.safetensors": (
        212758388,
        "2a7d6c924b3cd12fb99a09280ca9c33a89c5d60b93253617d2e088c1a40374d9",
    ),
}


class AdapterError(Exception):
    """Only fixed machine codes may leave this boundary."""


def runtime_version() -> str:
    try:
        version = importlib.metadata.version("docling-slim")
    except importlib.metadata.PackageNotFoundError as exc:
        raise AdapterError("runtime_unavailable") from exc
    if version != VERSION:
        raise AdapterError("runtime_unavailable")
    return version


def convert(path: Path, models: Path) -> tuple[str, dict[str, Any]]:
    runtime_version()
    if any(os.environ.get(key) != value for key, value in OFFLINE_ENV.items()):
        raise AdapterError("runtime_unavailable")
    from docling.datamodel.accelerator_options import (
        AcceleratorDevice,
        AcceleratorOptions,
    )
    from docling.datamodel.base_models import ConversionStatus, InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import ContentLayer, ImageRefMode

    options = PdfPipelineOptions(
        artifacts_path=models,
        enable_remote_services=False,
        allow_external_plugins=False,
        do_ocr=False,
        do_table_structure=True,
        do_code_enrichment=False,
        do_formula_enrichment=False,
        do_picture_classification=False,
        do_picture_description=False,
        do_chart_extraction=False,
        generate_page_images=False,
        generate_picture_images=False,
        generate_table_images=False,
        generate_parsed_pages=False,
        document_timeout=PIPELINE_SECONDS,
        accelerator_options=AcceleratorOptions(
            num_threads=2, device=AcceleratorDevice.CPU
        ),
        layout_batch_size=1,
        table_batch_size=1,
        ocr_batch_size=1,
    )
    import torch

    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
    )
    result = converter.convert(
        path,
        raises_on_error=False,
        max_num_pages=MAX_PAGES,
        max_file_size=MAX_FILE_BYTES,
    )
    if result.status != ConversionStatus.SUCCESS:
        raise AdapterError("incomplete")
    pages = result.input.page_count
    if (
        not isinstance(pages, int)
        or not 1 <= pages <= MAX_PAGES
        or len(result.pages) != pages
    ):
        raise AdapterError("incomplete")
    markdown = result.document.export_to_markdown(
        image_mode=ImageRefMode.PLACEHOLDER,
        included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE},
    )
    if (
        not isinstance(markdown, str)
        or len(markdown.encode("utf-8")) > MAX_MARKDOWN_BYTES
    ):
        raise AdapterError("output_limit")
    # An image placeholder is not extracted text. OCR is deliberately unavailable.
    if not markdown.replace("<!-- image -->", "").strip():
        raise AdapterError("no_text")
    return markdown, {
        "engine": "docling",
        "version": VERSION,
        "profile": PROFILE,
        "page_count": pages,
        "ocr": False,
        "warnings": [],
    }
