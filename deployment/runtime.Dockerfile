# Build recipe only. Building/exporting/provisioning this image is a separate
# operator action; this file never grants the web process container API access.
# Supply immutable official python:3.12.14-slim-bookworm and uv:0.12.19 digests.
ARG PYTHON_IMAGE
ARG UV_IMAGE
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS runtime
ARG PYTHON_IMAGE
ARG UV_IMAGE
RUN case "$PYTHON_IMAGE" in *@sha256:*) ;; *) exit 2;; esac \
 && case "$UV_IMAGE" in *@sha256:*) ;; *) exit 2;; esac
ENV ORT_DISABLE_TELEMETRY=1 HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 \
    UV_PYTHON_DOWNLOADS=never PIP_NO_CACHE_DIR=1
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libseccomp2 libglib2.0-0 libgomp1 libgl1 fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*
# Constraints select only the normal converter dependency closure, not the
# service, tests, web server or every optional MarkItDown converter/plugin.
COPY requirements-web.lock /build/requirements-web.lock
COPY packages/markitdown /build/packages/markitdown
RUN sed '/^-e /d' /build/requirements-web.lock > /build/constraints.txt \
 && uv pip install --system --no-cache \
      --constraint /build/constraints.txt \
      '/build/packages/markitdown[pdf,docx,xlsx]' bleach markdown-it-py
COPY scripts/docling/pyproject.toml scripts/docling/uv.lock /build/docling/
# --copies is essential: interpreter symlinks must not point outside the
# exported image when the parent validates the fixed executable before launch.
RUN python3 -m venv --copies /opt/docling \
 && UV_PROJECT_ENVIRONMENT=/opt/docling uv sync --project /build/docling \
      --python /usr/local/bin/python3.12 --no-python-downloads \
      --frozen --no-dev --no-build \
 && python3 -c 'import shutil;shutil.copyfile("/usr/local/bin/python3.12","/opt/docling/bin/python")' \
 && chmod 755 /opt/docling/bin/python \
 && ln -s ../local/bin/python3.12 /usr/bin/python3
# Only approved code and exact model files are later bound by the runner.
# No model download, account database, home, key, token, input or service code
# is copied into this image. Preserve installed package license metadata.
RUN python3 -c 'import importlib.metadata as m; assert m.version("markitdown"); import onnxruntime; onnxruntime.disable_telemetry_events()' \
 && /opt/docling/bin/python -c 'import importlib.metadata as m; assert m.version("docling-slim")=="2.133.0"; assert m.version("docling-core")=="2.99.0"' \
 && rm -rf /build /root/.cache /root/.local /usr/local/bin/uv \
 && find /tmp -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + \
 && mkdir -p /input /output /code /models /tmp /proc /dev \
 && chmod 1777 /tmp
# Cleanup follows all smoke imports and includes hidden temporary entries.
# Docker create adds its own /dev init-layer scaffolding AFTER this build.
# The CI export preparer removes only fixed ephemeral targets from its fresh,
# trusted export; the application's strict empty/nonsymlink guard is unchanged.
# Export the stopped image filesystem; do not run a container per conversion.
# The operator adds markitdown-runtime.json to the exported root after recording
# the immutable image ID. See docs/SANDBOX-RUNTIME.md for gates and limitations.
