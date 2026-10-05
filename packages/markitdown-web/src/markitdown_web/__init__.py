"""Local-only web companion; upstream MarkItDown stays unchanged."""

import os

# Must precede any import of MarkItDown/Magika/ONNX Runtime. Calling ORT's
# disable_telemetry_events() after import is too late for initialization events.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"

__version__ = "0.2.0"
