"""Fast standard-library tests; no model downloads or ML imports."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import download_models
import resource_guard


class ResourceTests(unittest.TestCase):
    def test_hardlinks_count_once_and_symlinks_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original"
            source.write_bytes(b"test")
            (root / "hardlink").hardlink_to(source)
            (root / "symlink").symlink_to(source)
            expected = max(source.stat().st_size, source.stat().st_blocks * 512)
            self.assertEqual(resource_guard.tree_bytes(root), expected)

    def test_disk_budget_fails_closed(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                resource_guard, "tree_bytes", return_value=9 * resource_guard.GIB
            ),
            self.assertRaisesRegex(RuntimeError, "budget"),
        ):
            resource_guard.check(Path(directory))

    def test_free_disk_reserve(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            resource_guard.shutil, "disk_usage"
        ) as usage:
            usage.return_value.free = 10 * resource_guard.GIB
            with self.assertRaisesRegex(RuntimeError, "free"):
                resource_guard.check(Path(directory), reserve=1)


class ManifestTests(unittest.TestCase):
    def test_pinned_inventory(self):
        manifest = json.loads((HERE / "models.json").read_text())
        self.assertEqual(len(manifest["models"]), 5)
        self.assertEqual(sum(item["bytes"] for item in manifest["models"]), 384428156)
        for item in manifest["models"]:
            self.assertEqual(len(item["revision"]), 40)
            self.assertEqual(len(item["sha256"]), 64)
            self.assertNotIn("..", Path(item["local_path"]).parts)

    def test_integrity_and_extra_file_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "model").write_bytes(b"test")
            manifest = {
                "total_model_bytes": 4,
                "models": [
                    {
                        "local_path": "model",
                        "bytes": 4,
                        "sha256": hashlib.sha256(b"test").hexdigest(),
                    }
                ],
            }
            self.assertEqual(len(download_models.verify(root, manifest)), 1)
            (root / "extra").write_text("unapproved")
            with self.assertRaisesRegex(RuntimeError, "file set"):
                download_models.verify(root, manifest)
            (root / "extra").unlink()
            (root / "model").write_bytes(b"edit")
            with self.assertRaisesRegex(RuntimeError, "integrity"):
                download_models.verify(root, manifest)

    def test_model_root_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "actual").mkdir()
            (root / "models").symlink_to(root / "actual", target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, "symlink"):
                download_models.verify(root / "models", {"models": []})

    def test_unexpected_redirect_rejected(self):
        redirect = download_models.HTTPSRedirects()
        for url in [
            "http://huggingface.co/file",
            "https://example.org/file",
            "https://huggingface.co.evil.invalid/file",
        ]:
            with self.assertRaisesRegex(RuntimeError, "redirect"):
                redirect.redirect_request(None, None, 302, "", {}, url)

    def test_provenance_copies_match_manifest(self):
        root = HERE / "provenance"
        for entry in json.loads((root / "sources.json").read_text()):
            data = (root / entry["path"]).read_bytes()
            self.assertEqual(len(data), entry["bytes"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])


if __name__ == "__main__":
    unittest.main()
