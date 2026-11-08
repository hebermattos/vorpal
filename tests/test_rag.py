"""Offline fixtures for standalone local retrieval; no downloads or TensorFlow."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import rag
from config import ConfigError, validate_config


class FakeEncoder:
    def encode(self, text, normalize_embeddings=True):
        # Deterministic local fixture, not a semantic production embedding model.
        terms = {word.casefold() for word in rag.TOKEN.findall(text)}
        return Vector([float("nmap" in terms), float("python" in terms)])


class Vector(list):
    def tolist(self):
        return list(self)


class RagTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "corpus"
        self.root.mkdir()
        self.db = Path(self.tmp.name) / "index.sqlite3"
        self.settings = dict(rag.SETTINGS, min_text_length=10, chunk_chars=128,
                             chunk_overlap=20, top_k=3)
        (self.root / "nmap.txt").write_text("Nmap identifies open TCP ports and network services.\n"
                                             "Scan only explicitly approved hosts.\n")
        (self.root / "python.md").write_text("Python uses virtual environments for package isolation.\n")

    def test_text_search_returns_source_line_hash(self):
        result = rag.index(self.root, self.db, settings=self.settings)
        self.assertEqual(result["documents"], 2)
        found = rag.search("nmap TCP", self.db, settings=self.settings)
        self.assertEqual(found[0]["source"], "nmap.txt")
        self.assertEqual(found[0]["line"], 1)
        self.assertEqual(len(found[0]["sha256"]), 64)
        self.assertIn("Nmap", found[0]["text"])

    def test_incremental_update_and_deletion(self):
        rag.index(self.root, self.db, settings=self.settings)
        self.assertEqual(rag.index(self.root, self.db, settings=self.settings)["updated"], 0)
        (self.root / "nmap.txt").write_text("RustScan quickly discovers TCP ports for authorized targets.\n")
        self.assertEqual(rag.index(self.root, self.db, settings=self.settings)["updated"], 1)
        self.assertEqual(rag.search("Nmap", self.db, settings=self.settings), [])
        (self.root / "python.md").unlink()
        self.assertEqual(rag.index(self.root, self.db, settings=self.settings)["removed"], 1)
        self.assertEqual(rag.search("Python", self.db, settings=self.settings), [])

    def test_unsafe_and_internal_are_not_indexed(self):
        (self.root / "credentials.txt").write_text("This file should never be used for indexing!\n")
        (self.root / "manifest.json").write_text(json.dumps({"train_documents": ["secret"]}))
        (self.root / ".env").write_text("ADMIN_PASSWORD=this_is_not_safe_to_index\n")
        (self.root / "link.txt").symlink_to(self.root / "nmap.txt")
        result = rag.index(self.root, self.db, settings=self.settings)
        self.assertEqual(result["documents"], 2)

    def test_query_syntax_is_escaped(self):
        rag.index(self.root, self.db, settings=self.settings)
        self.assertIsInstance(rag.search('nmap " OR *', self.db, settings=self.settings), list)
        self.assertEqual(rag.search("???", self.db, settings=self.settings), [])
        with self.assertRaisesRegex(ValueError, "non-empty"):
            rag.search(" ", self.db, settings=self.settings)

    def test_hybrid_uses_local_vectors(self):
        with patch.object(rag, "_encoder", return_value=FakeEncoder()):
            result = rag.index(self.root, self.db, settings=self.settings, hybrid=True)
            self.assertEqual(result["mode"], "hybrid")
            found = rag.search("nmap", self.db, settings=self.settings)
            self.assertEqual(found[0]["source"], "nmap.txt")
        with sqlite3.connect(self.db) as db:
            self.assertGreater(db.execute("SELECT count(*) FROM vectors").fetchone()[0], 0)
        with self.assertRaisesRegex(ValueError, "mode/model changed"):
            rag.index(self.root, self.db, settings=self.settings, hybrid=False)

    def test_portable_bm25_fallback(self):
        rag.index(self.root, self.db, settings=self.settings)
        with sqlite3.connect(self.db) as db:
            ranked = rag._fallback_bm25(db, ["nmap", "TCP"], 8)
        self.assertTrue(ranked)
        with patch.object(rag, "_has_fts5", return_value=False):
            found = rag.search("nmap TCP", self.db, settings=self.settings)
        self.assertEqual(found[0]["source"], "nmap.txt")

    def test_config_validation(self):
        config = dict(rag.SETTINGS, chunk_overlap=1000)
        with self.assertRaises(ConfigError):
            validate_config("rag", config)
        config = dict(rag.SETTINGS, database="../outside.sqlite3")
        with self.assertRaises(ConfigError):
            validate_config("rag", config)


if __name__ == "__main__":
    unittest.main()
