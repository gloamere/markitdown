#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-.venv/bin/python}"
# ONNX Runtime 1.29+ needs the opt-out before Python imports it, including tests.
export ORT_DISABLE_TELEMETRY=1
"$PYTHON" -m ruff check packages/markitdown-web scripts/docling scripts/evaluate_web.py scripts/stage-desktop-downloads.py scripts/verify-desktop-service.py scripts/verify-product-site.py scripts/verify-mac-package.py
"$PYTHON" -m ruff check --config packages/markitdown-web/pyproject.toml scripts/ci scripts/verify_v1_docling.py
"$PYTHON" -m black --check packages/markitdown-web scripts/docling scripts/ci scripts/evaluate_web.py scripts/verify_v1_docling.py scripts/stage-desktop-downloads.py scripts/verify-desktop-service.py scripts/verify-product-site.py scripts/verify-mac-package.py
"$PYTHON" -m mypy --follow-imports=silent --config-file packages/markitdown-web/pyproject.toml packages/markitdown-web/src
"$PYTHON" -m pytest -q packages/markitdown-web/tests
"$PYTHON" scripts/evaluate_web.py --out .venv/synthetic-evaluation
"$PYTHON" packages/markitdown-web/tests/browser_e2e.py --self-check
"$PYTHON" -m pytest -q scripts/docling/tests
bash -n scripts/docling/install.sh
bash -n scripts/ci/production-sandbox.sh scripts/ci/build-bubblewrap.sh
# Local-format regression coverage only: exclude the upstream arXiv URL fetch.
"$PYTHON" -m pytest -q packages/markitdown/tests/test_csv.py packages/markitdown/tests/test_docx.py packages/markitdown/tests/test_xlsx.py packages/markitdown/tests/test_pdf.py -k 'not test_markitdown_remote'
if command -v node >/dev/null 2>&1; then
  node --check packages/markitdown-web/src/markitdown_web/static/app.js
  node --check packages/markitdown-web/src/markitdown_web/static/site.js
  node packages/markitdown-web/tests/frontend_dom.cjs
  node packages/markitdown-web/tests/site_dom.cjs
fi
