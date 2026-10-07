"""Independent resource-bounded Markdown preview, never parser-supplied HTML."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

TAGS = {
    "p",
    "br",
    "hr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "strong",
    "em",
    "s",
    "blockquote",
    "ul",
    "ol",
    "li",
    "pre",
    "code",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "a",
}


def render_preview(markdown: str) -> str:
    # Called only by the independent renderer process in application flows.
    # Tests may call this pure helper directly with small synthetic examples.
    import bleach
    from markdown_it import MarkdownIt

    renderer = MarkdownIt("commonmark", {"html": False, "maxNesting": 30}).enable(
        "table"
    )
    return bleach.clean(
        renderer.render(markdown), tags=TAGS, attributes={}, protocols=[], strip=True
    )


def validate_html(html: str) -> bool:
    """Linear bounded grammar check: fixed tags, no attributes or declarations.

    There is no HTML tree construction, regex backtracking, entity expansion,
    network fetching or trusting arbitrary parser HTML in the supervisor.
    """
    allowed = TAGS | {"/" + tag for tag in TAGS}
    for segment in html.split("<")[1:]:
        end = segment.find(">")
        if end < 0 or segment[:end] not in allowed:
            return False
    return True


def worker_main() -> None:
    from . import sandbox
    from .conversion import MAX_HTML_BYTES, MAX_MARKDOWN_BYTES, MAX_RESULT_BYTES

    secured = os.environ.get("MARKITDOWN_SANDBOX_PROFILE") == sandbox.PROFILE
    if sys.platform == "linux":
        import ctypes
        import signal

        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise RuntimeError("Renderer parent death enforcement unavailable")
        expected = os.environ.get("MARKITDOWN_PARENT_PID")
        if expected and os.getppid() != int(expected):
            raise RuntimeError("Renderer parent changed")
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
        resource.setrlimit(resource.RLIMIT_AS, (384 * 1024**2,) * 2)
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_RESULT_BYTES,) * 2)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
        if secured:
            resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))
    except (ImportError, OSError, ValueError):
        if secured:
            raise
    if secured:
        sandbox.enforce_worker_filter()
    source, _, output = sys.argv[1:]
    result: dict[str, object]
    try:
        with Path(source).open("rb") as handle:
            data = handle.read(MAX_MARKDOWN_BYTES + 1)
        if len(data) > MAX_MARKDOWN_BYTES:
            raise ValueError("Markdown too large")
        html = render_preview(data.decode("utf-8"))
        if len(html.encode("utf-8")) > MAX_HTML_BYTES:
            raise ValueError("Preview too large")
        result = {"html": html}
    except Exception:
        result = {"error": "preview_failed"}
    Path(output).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    worker_main()
