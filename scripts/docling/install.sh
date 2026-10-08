#!/usr/bin/env bash
# Reproduce the isolated Linux x86_64 CPU runtime without changing web-app dependencies.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME="${1:-/workspace/shared/docling-preview}"
PYTHON="${PYTHON:-python3}"
command -v uv >/dev/null || { echo 'Install uv from its official source before running this script' >&2; exit 1; }
mkdir -p "$RUNTIME"
RUNTIME="$(cd -- "$RUNTIME" && pwd)"
"$PYTHON" - "$SCRIPT_DIR" "$RUNTIME" <<'PY'
from pathlib import Path
import platform, shutil, sys
script, root = map(Path, sys.argv[1:])
repo = script.parents[1]
if root == repo or repo in root.parents:
    raise SystemExit('Keep runtime and weights outside the Git repository')
if platform.system() != 'Linux' or platform.machine() != 'x86_64':
    raise SystemExit('This lock targets Linux x86_64 only')
free = shutil.disk_usage(root).free
available = next((int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')), 0)
print(f'Preflight: {free / 1024**3:.2f} GiB free disk; {available / 1024**3:.2f} GiB available RAM')
if available < 2 * 1024**3:
    raise SystemExit('At least 2 GiB available RAM is required for installation')
PY
export UV_CACHE_DIR="$RUNTIME/.uv-cache"
export UV_PROJECT_ENVIRONMENT="$RUNTIME/.venv"
export ORT_DISABLE_TELEMETRY=1
export HF_HUB_DISABLE_TELEMETRY=1
export DO_NOT_TRACK=1
# No source builds, implicit CUDA wheel index, default Docling extras, or lock updates.
"$PYTHON" "$SCRIPT_DIR/resource_guard.py" "$RUNTIME" -- \
  uv sync --project "$SCRIPT_DIR" --python 3.12.14 --no-python-downloads --frozen --no-dev --no-build
"$PYTHON" "$SCRIPT_DIR/download_models.py" "$RUNTIME"
"$RUNTIME/.venv/bin/python" "$SCRIPT_DIR/generate_fixtures.py" "$RUNTIME/fixtures"
"$RUNTIME/.venv/bin/python" "$SCRIPT_DIR/audit_runtime.py" "$RUNTIME"
printf '\nReady: %s\nModels: %s\nFixtures: %s\n' "$RUNTIME/.venv/bin/python" "$RUNTIME/models" "$RUNTIME/fixtures"
