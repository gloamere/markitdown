# Isolated Docling CPU runtime

This optional runtime is independent of the normal MarkItDown/web installation.
It targets Linux x86_64 and Python 3.12.14, uses the frozen `uv.lock`, and keeps
packages, caches, models, generated fixtures, and audit results outside Git.
The runtime was provisioned using uv 0.12.19.

## Reproduce

From the repository root, with Python 3.12.14 and uv already available:

```sh
scripts/docling/install.sh /workspace/shared/docling-preview
```

The installer uses `uv sync --frozen --no-dev --no-build` and does not update the
lock or build source distributions. `torch` and `torchvision` are explicitly
restricted to `https://download.pytorch.org/whl/cpu`; every other package comes
from `https://pypi.org/simple`. No environment variables containing credentials
or model-hub tokens are required. Model downloads use fixed public HTTPS resolve
URLs, immutable revisions, exact lengths, and SHA-256 checksums. The download
client rejects non-HTTPS or non-Hugging-Face CDN redirects and does not add an
Authorization header. It honors the environment's normal network/proxy policy.

Before installation, the script checks available memory and disk. During package
installation and model download it checks the 8 GiB runtime disk budget and
preserves at least 10 GiB free disk. The package supervisor terminates its process
group if a limit is crossed. These are sampled disk guards, not a filesystem
quota; other concurrent processes can independently consume disk. Runtime
conversion CPU, memory, network, and execution time limits belong to the web
worker and are separate from the installation checks.

The script is repeatable: matching existing models are verified and reused;
modified existing model files cause a failure instead of being silently replaced.
Do not point the runtime directory inside the Git repository.

## Package scope

| Package | Pinned version |
|---|---|
| docling-slim | 2.133.0, extras `convert-core,format-pdf,models-local` |
| docling-core | 2.99.0 |
| docling-ibm-models | 4.0.3 |
| docling-parse | 7.22.2 |
| transformers | 5.18.0 |
| torch | 2.14.1+cpu |
| torchvision | 0.29.1+cpu |
| opencv-python-headless | 4.14.0.94 |
| reportlab | 4.5.1, synthetic fixtures only |
| bleach | 6.4.0, sanitized HTML preview |
| markdown-it-py | 4.2.0, Markdown preview parsing |

The lock has 75 installed distributions plus its non-installed virtual project.
The full `docling` metapackage, OCR extras, ONNX Runtime, CUDA/NVIDIA packages,
and VLM extras are not installed. Headless OpenCV is present because the
TableFormer path needs it. The Transformers package contains general model code;
no OCR/VLM model weights are provisioned.

`ORT_DISABLE_TELEMETRY=1` is set before Docling imports, together with other
telemetry/offline settings in the audit and the conversion worker. The installer
necessarily accesses the network to fetch packages and pinned model files.
Auditing imports and verifies the installed runtime without model downloads.

## Model inventory and licensing

Exactly five model files total **384,428,156 bytes**, specified in `models.json`:

- Heron layout: `docling-project/docling-layout-heron`, revision
  `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8`, Apache-2.0
  - `config.json`, `preprocessor_config.json`, `model.safetensors`
- TableFormer accurate: `docling-project/docling-models`, revision
  `fc0f2d45e2218ea24bce5045f58a389aed16dc23` (`v2.3.0`), CDLA-Permissive-2.0
  - `model_artifacts/tableformer/accurate/tm_config.json`
  - `model_artifacts/tableformer/accurate/tableformer_accurate.safetensors`

The TableFormer SHA-256 is
`2a7d6c924b3cd12fb99a09280ca9c33a89c5d60b93253617d2e088c1a40374d9`,
verified against the official Hugging Face LFS metadata at the pinned revision.

Exact upstream model cards and license text copies are retained in `provenance/`.
`sources.json` records their download URLs, sizes, and hashes. The Heron repository
model card declares Apache-2.0; its license text copy is from Apache. The Docling
Models card declares CDLA-Permissive-2.0; the license text copy is from the tagged
SPDX license-list-data v3.25.0 release. The cards and licenses are copied to the
external runtime alongside the model manifest. The model tree has read-only
files (0444) and directories (0555). This is an accidental-write safeguard;
the owning user can still change those modes.

No weights are checked into this repository. Retain applicable license notices
when redistributing the model files; package licenses are in their installed
wheel metadata.

## Verify

```sh
RUNTIME=/workspace/shared/docling-preview
"$RUNTIME/.venv/bin/python" scripts/docling/audit_runtime.py "$RUNTIME"
"$RUNTIME/.venv/bin/python" scripts/docling/verify_fixtures.py "$RUNTIME/fixtures"
python3 -m unittest discover -s scripts/docling/tests -v
```

`runtime-audit.json` and `installed-versions.txt` are generated in the runtime.
The audit checks installed versions against the lock, package source indexes,
CPU-only Torch, imports, model checksums and permissions, retained provenance,
and remaining disk. It does not claim application conversion benchmarks passed.
The root application tests and actual adapter/worker runs supply those results.

## Synthetic fixtures

`generate_fixtures.py` creates deterministic ReportLab/Pillow fixtures without
external source material. `fixtures/manifest.json` records page counts, expected
text, table contents, byte lengths, and SHA-256 hashes:

- `text_1page.pdf`: selectable text
- `columns_table_1page.pdf`: two columns plus a simple ruled table
- `columns_table_2pages.pdf`: the same layout on two pages
- `text_3pages.pdf`: page-limit rejection case for the two-page preview
- `image_only_1page.pdf`: an image of text with zero PDF text-layer characters,
  for the OCR-disabled path

`verify_fixtures.py` checks PDF page counts and text layers with PDFium. Add
`--render` to render each page to PNG under `fixtures/renders/` for visual QA.
These controlled synthetic files are smoke tests, not a diverse PDF benchmark.
