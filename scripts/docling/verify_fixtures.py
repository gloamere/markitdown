#!/usr/bin/env python3
"""Verify synthetic PDF fixtures and optionally render pages for visual review."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pypdfium2 as pdfium


def verify(root: Path, render: bool = False) -> list[dict]:
    manifest = json.loads((root / "manifest.json").read_text())
    result = []
    renders = root / "renders"
    if render:
        renders.mkdir(exist_ok=True)
    for entry in manifest["fixtures"]:
        path = root / entry["file"]
        data = path.read_bytes()
        if (
            len(data) != entry["bytes"]
            or hashlib.sha256(data).hexdigest() != entry["sha256"]
        ):
            raise RuntimeError(f"Fixture checksum failed: {path.name}")
        with pdfium.PdfDocument(str(path)) as pdf:
            if len(pdf) != entry["pages"]:
                raise RuntimeError(f"Wrong page count: {path.name}")
            texts = []
            for index in range(len(pdf)):
                page = pdf[index]
                textpage = page.get_textpage()
                text = textpage.get_text_range()
                texts.append(text)
                if render:
                    bitmap = page.render(scale=1)
                    bitmap.to_pil().save(renders / f"{path.stem}-{index+1}.png")
                    bitmap.close()
                textpage.close()
                page.close()
            alltext = "\n".join(texts)
            if not entry["text_layer"] and alltext.strip():
                raise RuntimeError(
                    f"Unexpected text in image-only fixture: {path.name}"
                )
            if any(snippet not in alltext for snippet in entry["expected_text"]):
                raise RuntimeError(f"Missing expected text: {path.name}")
            result.append(
                {"file": path.name, "pages": len(pdf), "text_characters": len(alltext)}
            )
    (root / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixtures", type=Path)
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify(args.fixtures, args.render), indent=2))
