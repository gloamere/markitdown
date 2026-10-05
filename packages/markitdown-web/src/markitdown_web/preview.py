"""Inert Markdown preview, called inside the resource-bounded worker."""

import bleach
from markdown_it import MarkdownIt


def render_preview(markdown: str) -> str:
    renderer = MarkdownIt("commonmark", {"html": False, "maxNesting": 30}).enable(
        "table"
    )
    return bleach.clean(
        renderer.render(markdown),
        tags={
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
        },
        attributes={},
        protocols=[],
        strip=True,
    )
