"""Offline tests for read-only corpus quality audit."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from data_quality import audit


class DataQualityTests(unittest.TestCase):
    def test_duplicates_and_valid_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus, out = root / "corpus", root / "data"
            corpus.mkdir()
            out.mkdir()
            text = "A harmless local documentation paragraph for testing data quality.\n"
            (corpus / "one.txt").write_text(text, encoding="utf-8")
            (corpus / "two.txt").write_text(text + "\n", encoding="utf-8")
            digest = hashlib.sha256(text.encode()).hexdigest()
            (out / "split_state.json").write_text(json.dumps({
                "train_document_hashes": [digest], "valid_document_hashes": []}), encoding="utf-8")
            result = audit(corpus, out)
            self.assertTrue(result["integrity_passed"])
            self.assertEqual(result["counts"]["duplicate"], 1)

    def test_manifest_missing_binary_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus, out = root / "corpus", root / "data"
            corpus.mkdir()
            out.mkdir()
            (out / "split_state.json").write_text(json.dumps({
                "train_document_hashes": [], "valid_document_hashes": []}), encoding="utf-8")
            (out / "manifest.json").write_text(json.dumps({
                "train_document_hashes": [], "valid_document_hashes": [],
                "train_bytes": 10, "valid_bytes": 0}), encoding="utf-8")
            result = audit(corpus, out)
            self.assertFalse(result["integrity_passed"])
            self.assertTrue(any("binary" in finding for finding in result["findings"]))


if __name__ == "__main__":
    unittest.main()
