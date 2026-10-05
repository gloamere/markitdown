#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-.venv/bin/python}"
# ONNX Runtime 1.29+ needs the opt-out before Python imports it, including tests.
export ORT_DISABLE_TELEMETRY=1
"$PYTHON" -m ruff check packages/markitdown-web
"$PYTHON" -m black --check packages/markitdown-web
"$PYTHON" -m mypy --follow-imports=silent --config-file packages/markitdown-web/pyproject.toml packages/markitdown-web/src
"$PYTHON" -m pytest -q packages/markitdown-web/tests
# Local-format regression coverage only: exclude the upstream arXiv URL fetch.
"$PYTHON" -m pytest -q packages/markitdown/tests/test_csv.py packages/markitdown/tests/test_docx.py packages/markitdown/tests/test_xlsx.py packages/markitdown/tests/test_pdf.py -k 'not test_markitdown_remote'
if command -v node >/dev/null 2>&1; then
  node --check packages/markitdown-web/src/markitdown_web/static/app.js
  node packages/markitdown-web/tests/frontend_dom.cjs
fi
