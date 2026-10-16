"""Cloud-verifiable safety checks. These tests do not validate Windows/CUDA."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from musicstudio import bootstrap, common, models


class IntegrityTests(unittest.TestCase):
    def test_path_cannot_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                common.safe_path(directory, "../other/model.bin")
            self.assertEqual(common.safe_path(directory, "vae/config.json").parent.name, "vae")

    def test_weights_with_right_size_wrong_contents_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weights.safetensors"
            original = b"trusted data"
            entry = dict(bytes=len(original), sha256=hashlib.sha256(original).hexdigest())
            path.write_bytes(original)
            self.assertTrue(models.matches(path, entry))
            path.write_bytes(b"untrust data")
            self.assertFalse(models.matches(path, entry))

    def test_git_blob_and_source_code_are_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            data = b'{"version":1}'
            path.write_bytes(data)
            entry = dict(bytes=len(data), sha256=None, git_blob=hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest())
            self.assertTrue(models.matches(path, entry))
            path.write_bytes(b'{"version":2}')
            self.assertFalse(models.matches(path, entry))
            source = dict(source_path="model.py", source_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertTrue(models.matches(path, source))

    def test_no_lm_selection_never_includes_lm_weights(self):
        with_lm = list(models.entries(True))
        no_lm = list(models.entries(False))
        self.assertEqual(len(with_lm), 27)
        self.assertEqual(len(no_lm), 16)
        self.assertTrue(all(not model["prefix"] for model, _, _ in no_lm))
        self.assertFalse(any("1.7B" in str(path) for _, _, path in with_lm))
        self.assertEqual(sum(f["bytes"] for _, f, _ in with_lm), 7709383012)
        for _, file, _ in with_lm:
            if file.get("source_path"):
                self.assertEqual(file["source_sha256"], common.read_json(common.ROOT / "instalacion/bootstrap.lock.json")["source_files"][file["source_path"]])

    def test_extract_wont_overwrite_user_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "instalacion").mkdir()
            archive = root / "source.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("source/file.py", "original")
            pin = {"source": {"sha256": common.sha256(archive)}}
            (root / "instalacion/bootstrap.lock.json").write_text(json.dumps(pin))
            with patch.object(bootstrap, "ROOT", root):
                bootstrap.extract_verified("source", archive)
                target = root / ".runtime/source/source/file.py"
                target.write_text("local change")
                with self.assertRaises(RuntimeError):
                    bootstrap.extract_verified("source", archive)
                self.assertEqual(target.read_text(), "local change")

    def test_bad_archive_hash_prevents_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "instalacion").mkdir()
            archive = root / "source.zip"
            archive.write_bytes(b"not a zip")
            (root / "instalacion/bootstrap.lock.json").write_text(json.dumps({"source": {"sha256": "0" * 64}}))
            with patch.object(bootstrap, "ROOT", root), self.assertRaises(RuntimeError):
                bootstrap.extract_verified("source", archive)
            self.assertFalse((root / ".runtime/source").exists())

    def test_archive_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "instalacion").mkdir()
            archive = root / "source.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../../../escape.py", "bad")
            (root / "instalacion/bootstrap.lock.json").write_text(json.dumps({"source": {"sha256": common.sha256(archive)}}))
            with patch.object(bootstrap, "ROOT", root), self.assertRaises(ValueError):
                bootstrap.extract_verified("source", archive)
            self.assertFalse((root / "escape.py").exists())


if __name__ == "__main__":
    unittest.main()
