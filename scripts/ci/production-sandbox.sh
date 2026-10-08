#!/usr/bin/env bash
# Intended for a fresh, authorized Linux CI runner with existing Docker access.
# Never changes kernel policy, container privileges, credentials or host grants.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHON="${PYTHON:-.venv/bin/python}"
ROOT=$(mktemp -d "${RUNNER_TEMP:?Run only in an explicitly provisioned disposable CI runner}/markitdown-production.XXXXXXXX")
command -v docker >/dev/null
BWRAP="${MARKITDOWN_SANDBOX_BWRAP:-/usr/bin/bwrap}"
test -x "$BWRAP"
# Content-free identity of this disposable test host and verified launcher.
uname -srm
"$BWRAP" --version
# Fail before expensive image work if existing namespace capability is denied.
"$BWRAP" --unshare-all --unshare-user --unshare-cgroup --die-with-parent --ro-bind /usr /usr \
  --ro-bind /lib /lib --ro-bind /lib64 /lib64 --proc /proc --dev /dev /usr/bin/true

# Resolve exact vendor version tags once, then build only from their immutable
# digests. The resolved identities are evidence, never guessed constants.
PYTHON_TAG=docker.io/library/python:3.12.14-slim-bookworm
UV_TAG=ghcr.io/astral-sh/uv:0.12.19
for ref in "$PYTHON_TAG" "$UV_TAG"; do docker pull --platform linux/amd64 "$ref"; done
PYTHON_IMAGE=$(docker image inspect "$PYTHON_TAG" --format '{{index .RepoDigests 0}}')
UV_IMAGE=$(docker image inspect "$UV_TAG" --format '{{index .RepoDigests 0}}')
"$PYTHON" - "$PYTHON_IMAGE" "$UV_IMAGE" "$ROOT/base-images.json" <<'PY'
import json, re, sys
python, uv, output = sys.argv[1:]
assert re.fullmatch(r'(?:docker.io/library/)?python@sha256:[a-f0-9]{64}', python), python
assert re.fullmatch(r'ghcr.io/astral-sh/uv@sha256:[a-f0-9]{64}', uv), uv
with open(output, 'w') as handle:
    json.dump({'python': python, 'uv': uv}, handle, indent=2)
PY
docker run --rm --network none --entrypoint python3 "$PYTHON_IMAGE" --version | grep -Fx 'Python 3.12.14'
docker run --rm --network none --entrypoint /uv "$UV_IMAGE" --version | grep -E '^uv 0\.12\.19( |$)'
docker build --pull=false --platform linux/amd64 \
  --build-arg "PYTHON_IMAGE=$PYTHON_IMAGE" --build-arg "UV_IMAGE=$UV_IMAGE" \
  -f deployment/runtime.Dockerfile -t "markitdown-runtime:${GITHUB_RUN_ID:-synthetic}" .
IMAGE=$(docker image inspect "markitdown-runtime:${GITHUB_RUN_ID:-synthetic}" --format '{{.Id}}')
CONTAINER=$(docker create "$IMAGE" /bin/true)
trap 'docker rm "$CONTAINER" >/dev/null 2>&1 || true' EXIT
test "$(docker container inspect "$CONTAINER" --format '{{.Image}}')" = "$IMAGE"
docker export "$CONTAINER" > "$ROOT/runtime.tar"
"$PYTHON" scripts/ci/prepare_runtime_export.py initialize --workspace "$ROOT" --image-id "$IMAGE"
# This is an export of the just-built pinned official image, never user input.
tar --extract --file "$ROOT/runtime.tar" --directory "$ROOT/runtime-root" --no-same-owner
rm "$ROOT/runtime.tar"
docker rm "$CONTAINER" >/dev/null
trap - EXIT
# Canonicalize this one-shot trusted export, not host directories or arbitrary
# user state. Docker init-layer mount scaffolding is not part of our runtime.
# Preparation retains source-image identity and emits content-free per-target
# evidence. A symlink or application state is a failure, never repaired away.
"$PYTHON" scripts/ci/prepare_runtime_export.py prepare --workspace "$ROOT" --image-id "$IMAGE"
# Required opt-in test: once configured, denial is failure, never a skip.
MARKITDOWN_TEST_RUNTIME_ROOT="$ROOT/runtime-root" \
  "$PYTHON" -m pytest -q packages/markitdown-web/tests/test_sandbox_contract.py \
    packages/markitdown-web/tests/test_production_lifecycle.py
# Exercise real standard formats and the independent preview inside production.
"$PYTHON" scripts/ci/verify_production_runtime.py --runtime-root "$ROOT/runtime-root" \
  --output "$ROOT/standard-runtime.json"
# Reuse only the existing five revision/checksum-pinned public model assets in a
# separate fresh build workspace. No token, model download inside a parser, or
# image/security-policy change is introduced by this bounded integration check.
mkdir "$ROOT/docling-test"
"$PYTHON" scripts/docling/download_models.py "$ROOT/docling-test"
"$PYTHON" scripts/ci/verify_production_docling.py \
  --runtime-root "$ROOT/runtime-root" --models "$ROOT/docling-test/models" \
  --image-id "$IMAGE" --output "$ROOT/docling-runtime.json"
# This job proves only its bounded checks, never dedicated target-host capacity,
# TLS, quality across a corpus or production deployment acceptance.
cat "$ROOT/base-images.json" "$ROOT/runtime-preparation.json" "$ROOT/standard-runtime.json" "$ROOT/docling-runtime.json"
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo '### Production sandbox CI evidence'
    echo 'Executed fixed isolation/lifecycle checks and real standard/Docling/preview paths.'
    echo 'No kernel/security settings changed; no privileged containers or secrets used.'
    echo 'Target-host capacity/TLS/deployment and broad quality remain separate gates.'
    echo '```json'; cat "$ROOT/base-images.json" "$ROOT/runtime-preparation.json" "$ROOT/standard-runtime.json" "$ROOT/docling-runtime.json"; echo '```'
  } >> "$GITHUB_STEP_SUMMARY"
fi
