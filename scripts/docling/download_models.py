#!/usr/bin/env python3
"""Fetch only five revision-pinned, checksum-verified public model files."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import urllib.parse
import urllib.request
from pathlib import Path

from resource_guard import check

HERE = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class HTTPSRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlparse(newurl)
        host = target.hostname or ""
        if target.scheme != "https" or not (
            host == "huggingface.co" or host.endswith((".huggingface.co", ".hf.co"))
        ):
            raise RuntimeError(f"Unexpected model download redirect host: {host}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def verify(models: Path, manifest: dict) -> list[dict]:
    if models.is_symlink() or any(path.is_symlink() for path in models.rglob("*")):
        raise RuntimeError("Model tree must not contain symlinks")
    expected = {item["local_path"] for item in manifest["models"]}
    actual = {
        str(path.relative_to(models)) for path in models.rglob("*") if path.is_file()
    }
    if actual != expected:
        raise RuntimeError(
            f"Model file set differs: missing={expected - actual}; extra={actual - expected}"
        )
    result = []
    for item in manifest["models"]:
        path = models / item["local_path"]
        if (
            path.is_symlink()
            or path.stat().st_size != item["bytes"]
            or sha256(path) != item["sha256"]
        ):
            raise RuntimeError(f"Model integrity check failed: {item['local_path']}")
        result.append(
            {
                "path": item["local_path"],
                "bytes": item["bytes"],
                "sha256": item["sha256"],
            }
        )
    if sum(item["bytes"] for item in result) != manifest["total_model_bytes"]:
        raise RuntimeError("Model total does not match the manifest")
    return result


def install(root: Path) -> None:
    manifest = json.loads((HERE / "models.json").read_text())
    models = root / "models"
    if models.is_symlink():
        raise RuntimeError("Model root must not be a symlink")
    models.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(HTTPSRedirects())
    for item in manifest["models"]:
        target = models / item["local_path"]
        if any(path.is_symlink() for path in (target, *target.parents)):
            raise RuntimeError(f"Model destination must not contain symlinks: {target}")
        if target.exists():
            if (
                target.is_symlink()
                or target.stat().st_size != item["bytes"]
                or sha256(target) != item["sha256"]
            ):
                raise RuntimeError(
                    f"Existing model is invalid; refusing to replace it: {target}"
                )
            print(f"Verified existing {item['local_path']}", flush=True)
            continue
        check(root, reserve=item["bytes"])
        target.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://huggingface.co/{item['repository']}/resolve/{item['revision']}/{item['path']}"
        temporary = target.with_name(target.name + ".part")
        if temporary.exists():
            raise RuntimeError(
                f"Partial download exists; inspect it before retrying: {temporary}"
            )
        digest = hashlib.sha256()
        count = 0
        try:
            # Deliberately no token, Authorization header, hf_hub_download, or mutable revision.
            request = urllib.request.Request(
                url, headers={"User-Agent": "markitdown-docling-runtime/1"}
            )
            with opener.open(request, timeout=120) as response, temporary.open(
                "xb"
            ) as output:
                while block := response.read(1024 * 1024):
                    count += len(block)
                    if count > item["bytes"]:
                        raise RuntimeError(
                            f"Download exceeded pinned size: {item['local_path']}"
                        )
                    output.write(block)
                    digest.update(block)
                    if count % (16 * 1024 * 1024) == 0:
                        check(root)
            if count != item["bytes"] or digest.hexdigest() != item["sha256"]:
                raise RuntimeError(
                    f"Download integrity check failed: {item['local_path']}"
                )
            temporary.replace(target)
        finally:
            # Only this invocation's incomplete scratch file is removed on failure.
            if temporary.exists():
                temporary.unlink()
        print(
            f"Downloaded and verified {item['local_path']} ({count} bytes)", flush=True
        )
    verified = verify(models, manifest)
    provenance = root / "provenance"
    provenance.mkdir(exist_ok=True)
    for path in (HERE / "provenance").iterdir():
        if path.is_file():
            shutil.copyfile(path, provenance / path.name)
    shutil.copyfile(HERE / "models.json", provenance / "models.json")
    (provenance / "verified-models.json").write_text(
        json.dumps(verified, indent=2) + "\n"
    )
    for path in sorted(models.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    models.chmod(0o555)
    check(root)
    print(
        f"Verified exactly {len(verified)} model files, {manifest['total_model_bytes']} bytes; model tree is read-only",
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.verify_only:
        print(
            json.dumps(
                verify(
                    args.runtime / "models",
                    json.loads((HERE / "models.json").read_text()),
                ),
                indent=2,
            )
        )
    else:
        install(args.runtime.resolve())
