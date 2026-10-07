"""Real standard-conversion/preview smoke inside the mandatory production profile."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from markitdown_web.conversion import ensure_standard_available, run_conversion
from markitdown_web.state import Settings

# Reuse the same locally generated inputs and immutable expected facts.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluate_web import fixtures  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = []
    with tempfile.TemporaryDirectory(prefix="markitdown-production-test-") as directory:
        root = Path(directory).resolve()
        settings = Settings(
            data_dir=root,
            deployment_mode="production",
            public_origin="https://synthetic.invalid",
            cookie_secure=True,
            sandbox_runtime_root=args.runtime_root.resolve(),
        )
        ensure_standard_available(settings)
        for index, (name, payload, expected) in enumerate(fixtures()):
            source = root / f"source-{index}{Path(name).suffix}"
            source.write_bytes(payload)
            markdown, html = run_conversion(source, source.suffix, settings=settings)
            missing = [fact for fact in expected if fact not in markdown]
            assert not missing, (name, missing)
            assert html and len(html.encode()) <= 4 * 1024**2
            cases.append(
                {
                    "name": name,
                    "passed": True,
                    "source_sha256": hashlib.sha256(payload).hexdigest(),
                    "markdown_sha256": hashlib.sha256(markdown.encode()).hexdigest(),
                }
            )
    report = {
        "profile": "linux-bwrap-v1",
        "passed": len(cases),
        "total": len(cases),
        "cases": cases,
        "limits": "Does not establish target host TLS, capacity or Docling production acceptance",
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
