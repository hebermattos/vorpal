"""Synthetic knowledge update tests: no network, training or scanning."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import knowledge
from config import ConfigError, validate_config


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        self.root = self.project / "corpus"
        self.root.mkdir()
        self.db = self.project / "data" / "rag.sqlite3"
        self.logs = self.project / "logs"
        self.document = self.root / "guide.md"
        self.document.write_text(
            "Nmap reports verified port states; services and versions remain tentative.\n",
            encoding="utf-8")
        self.root_patch = patch.object(knowledge, "ROOT", self.project)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def run_update(self, **kw):
        options = {"corpus": "corpus", "database": "data/rag.sqlite3",
                   "report_dir": "logs"}
        options.update(kw)
        return knowledge.update(**options)

    def test_incremental_update_and_report(self):
        with patch.object(knowledge, "import_directory", side_effect=AssertionError("network import")):
            first = self.run_update()
            self.assertEqual(first["changes"]["added"], ["guide.md"])
            self.assertEqual(first["index"]["documents"], 1)
            self.assertEqual(first["index"]["sqlite_integrity"], "ok")
            self.assertFalse(first["index"]["retrieval_quality_tested"])
            self.assertFalse(first["scan_started"])
            self.assertTrue((self.project / first["report"]).is_file())
            second = self.run_update()
            self.assertEqual(second["changes"], {"added": [], "updated": [], "removed": []})
            self.document.write_text(
                "Nmap version detection is a tentative observation, not a vulnerability.\n",
                encoding="utf-8")
            third = self.run_update()
            self.assertEqual(third["changes"]["updated"], ["guide.md"])
            self.document.unlink()
            fourth = self.run_update()
            self.assertEqual(fourth["changes"]["removed"], ["guide.md"])

    def test_import_is_opt_in_and_reuses_canonical_importer(self):
        def fake_import(root, output, force):
            self.assertFalse(force)
            self.assertEqual(output, self.root / "imported")
            output.mkdir()
            (output / "cached.txt").write_text(
                "Nuclei templates must be approved before execution.\n",
                encoding="utf-8")
            return ([{"source": "local fixture"}], [])
        with patch.object(knowledge, "import_directory", side_effect=fake_import) as importer:
            result = self.run_update(import_sources=True)
        self.assertEqual(result["import"]["accepted"], 1)
        self.assertEqual(result["changes"]["added"], ["guide.md", "imported/cached.txt"])
        importer.assert_called_once()

    def test_rejects_cross_corpus_index_reuse(self):
        self.run_update()
        other = self.project / "other"
        other.mkdir()
        (other / "new.md").write_text("Sufficient synthetic content for indexing.\n")
        with self.assertRaisesRegex(knowledge.KnowledgeError, "another corpus"):
            knowledge.update(corpus="other", database="data/rag.sqlite3", report_dir="logs")

    def test_rejects_database_inside_corpus_and_outside_paths(self):
        with self.assertRaisesRegex(knowledge.KnowledgeError, "outside the corpus"):
            self.run_update(database="corpus/rag.sqlite3")
        with self.assertRaisesRegex(knowledge.KnowledgeError, "inside the project"):
            self.run_update(database="../external.sqlite3")

    def test_validated_config(self):
        settings = dict(knowledge.SETTINGS, imported_subdir="../escape")
        with self.assertRaises(ConfigError):
            validate_config("knowledge", settings)
        settings = dict(knowledge.SETTINGS, imported_subdir="sub/path")
        with self.assertRaises(ConfigError):
            validate_config("knowledge", settings)


if __name__ == "__main__":
    unittest.main()
