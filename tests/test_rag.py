"""Offline fixtures for standalone local retrieval; no downloads or model server."""
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
        (self.root / "manifest.json").write_text(json.dumps({"metadata": "not a source document"}))
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

    def test_relevance_rejects_weak_lexical_and_stopword_only_matches(self):
        rag.index(self.root, self.db, settings=self.settings)
        for fts in (True, False):
            with self.subTest(fts=fts), patch.object(rag, "_has_fts5", return_value=fts):
                self.assertEqual(rag.search("the and for", self.db, settings=self.settings), [])
                self.assertEqual(rag.search("nmap apples pears bananas oranges grapes",
                                            self.db, settings=self.settings), [])
                self.assertTrue(rag.search("How can I use Nmap?", self.db, settings=self.settings))

    def test_hybrid_rank_alone_is_not_relevance(self):
        with patch.object(rag, "_encoder", return_value=FakeEncoder()):
            rag.index(self.root, self.db, settings=self.settings, hybrid=True)
            self.assertEqual(rag.search("unrelatedword", self.db, settings=self.settings), [])
            # Semantic retrieval remains available without lexical overlap.
            encoder = FakeEncoder()
            encoder.encode = lambda text, **kw: Vector([1.0, 0.0])
            with patch.object(rag, "_encoder", return_value=encoder):
                found = rag.search("networkmapper", self.db, settings=self.settings)
            self.assertEqual([item['source'] for item in found], ['nmap.txt'])

    def test_weak_and_nonfinite_semantic_matches_are_rejected(self):
        encoder = FakeEncoder()
        encoder.encode = lambda text, **kw: Vector([1.0, 0.0])
        with patch.object(rag, '_encoder', return_value=encoder):
            rag.index(self.root, self.db, settings=self.settings, hybrid=True)
            for vector in ([0.4, 0.916515], [float('nan'), 1.0]):
                encoder.encode = lambda text, **kw: Vector(vector)
                with self.subTest(vector=vector):
                    self.assertEqual(rag.search('unrelatedword', self.db, settings=self.settings), [])

    def test_one_failed_source_does_not_block_other_updates(self):
        rag.index(self.root, self.db, settings=self.settings)
        (self.root / 'python.md').write_text('Python updated package isolation guide for virtual environments.\n')
        original = Path.read_bytes
        def read(path):
            if path == self.root / 'nmap.txt':
                raise OSError('temporary source failure')
            return original(path)
        with patch.object(Path, 'read_bytes', read):
            result = rag.index(self.root, self.db, settings=self.settings)
        self.assertEqual(result['retained'], ['nmap.txt'])
        self.assertEqual(result['updated'], 1)
        self.assertIn('updated', rag.search('Python', self.db, settings=self.settings)[0]['text'])

    def test_newly_secret_bearing_content_evicts_old_chunks(self):
        rag.index(self.root, self.db, settings=self.settings)
        (self.root / 'nmap.txt').write_text('Nmap ADMIN_PASSWORD=this_is_not_safe_to_index\n')
        result = rag.index(self.root, self.db, settings=self.settings)
        self.assertEqual(result['rejected'], [{'source': 'nmap.txt', 'reason': 'secret_content'}])
        self.assertEqual(result['removed'], 1)
        self.assertEqual(rag.search('Nmap', self.db, settings=self.settings), [])

    def test_read_failure_retains_hash_chunks_and_vectors_then_retries(self):
        with patch.object(rag, "_encoder", return_value=FakeEncoder()):
            rag.index(self.root, self.db, settings=self.settings, hybrid=True)
            def snapshot():
                with sqlite3.connect(self.db) as db:
                    return [db.execute(sql).fetchall() for sql in (
                        "SELECT * FROM documents ORDER BY source",
                        "SELECT * FROM chunks ORDER BY id", "SELECT * FROM vectors ORDER BY chunk_id")]
            before = snapshot()
            path = self.root / 'nmap.txt'
            path.write_text('Nmap updated guide for local TCP network services.\n')
            original = Path.read_bytes
            def read(candidate):
                if candidate == path:
                    raise PermissionError('temporary fixture failure')
                return original(candidate)
            with patch.object(Path, 'read_bytes', read):
                result = rag.index(self.root, self.db, settings=self.settings, hybrid=True)
            self.assertEqual(result['retained'], ['nmap.txt'])
            self.assertEqual(result['removed'], 0)
            self.assertEqual(result['documents'], 2)
            self.assertEqual(result['failed'][0]['reason'], 'read_error')
            self.assertEqual(snapshot(), before)
            self.assertEqual(rag.index(self.root, self.db, settings=self.settings, hybrid=True)['updated'], 1)
            self.assertNotEqual(snapshot(), before)

    def test_stat_failure_preserves_existing_document(self):
        rag.index(self.root, self.db, settings=self.settings)
        original = Path.lstat
        def lstat(candidate, *args, **kwargs):
            if candidate == self.root / 'nmap.txt':
                raise OSError('temporary metadata failure')
            return original(candidate, *args, **kwargs)
        with patch.object(Path, 'lstat', lstat):
            result = rag.index(self.root, self.db, settings=self.settings)
        self.assertEqual(result['retained'], ['nmap.txt'])
        self.assertTrue(rag.search('Nmap', self.db, settings=self.settings))

    def test_rejected_content_is_evicted_and_deletions_are_reported(self):
        rag.index(self.root, self.db, settings=self.settings)
        (self.root / 'nmap.txt').write_bytes(b'\xff invalid UTF-8')
        (self.root / 'python.md').unlink()
        result = rag.index(self.root, self.db, settings=self.settings)
        self.assertEqual(result['removed'], 2)
        self.assertEqual(result['deleted'], ['python.md'])
        self.assertEqual(result['rejected'], [{'source': 'nmap.txt', 'reason': 'invalid_utf8'}])
        self.assertEqual(result['retained'], [])
        self.assertEqual(rag.search('Nmap', self.db, settings=self.settings), [])

    def test_incomplete_directory_inventory_rolls_back(self):
        rag.index(self.root, self.db, settings=self.settings)
        (self.root / 'nmap.txt').write_text('Changed Nmap text that must be rolled back.\n')
        def walk(root, *, followlinks, onerror):
            yield str(root), [], ['nmap.txt']
            onerror(PermissionError('cannot list subtree'))
        with patch.object(rag.os, 'walk', walk), self.assertRaisesRegex(OSError, 'Cannot enumerate corpus'):
            rag.index(self.root, self.db, settings=self.settings)
        self.assertIn('identifies', rag.search('Nmap', self.db, settings=self.settings)[0]['text'])
        self.assertTrue(rag.search('Python', self.db, settings=self.settings))

    def test_config_validation(self):
        config = dict(rag.SETTINGS, chunk_overlap=1000)
        with self.assertRaises(ConfigError):
            validate_config("rag", config)
        for key, value in [('min_lexical_coverage', 0), ('min_lexical_coverage', 1.1),
                           ('min_semantic_similarity', float('nan')),
                           ('min_semantic_similarity', True), ('stop_words', [False])]:
            config = dict(rag.SETTINGS, relevance=dict(rag.SETTINGS['relevance'], **{key: value}))
            with self.subTest(key=key, value=value), self.assertRaises(ConfigError):
                validate_config('rag', config)
        config = dict(rag.SETTINGS, database="../outside.sqlite3")
        with self.assertRaises(ConfigError):
            validate_config("rag", config)


if __name__ == "__main__":
    unittest.main()
