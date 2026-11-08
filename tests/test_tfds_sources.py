"""Declarative TFDS sources, official split isolation and training lifecycle."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from data import prepare
from prepare_wiki40b import DATASET, export_records
import tfds_sources
import train


class TFDSSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.corpus = self.root / 'corpus'
        self.corpus.mkdir()
        self.cache = self.root / 'cache'
        self.out = self.root / 'out'
        self.source = {'dataset': DATASET, 'out': str(self.cache)}
        self.settings = {'source_file': 'tfds', 'download_dir': str(self.root / 'downloads'), 'sources': [self.source]}
        self.texts = {split: (f'Official {split} article with independent content for a test of the training pipeline. ' * 8)
                      for split in ('test', 'validation', 'train')}
        records = {split: [{'wikidata_id': f'Q{i}', 'text': text, 'version_id': '1'}]
                   for i, (split, text) in enumerate(self.texts.items(), 1)}
        with contextlib.redirect_stdout(io.StringIO()):
            export_records(records, self.cache)
        (self.corpus / 'tfds').write_text(f'# Reviewed source\n\n{DATASET}\n{DATASET}\n')
        self.addCleanup(patch.stopall)
        patch.object(tfds_sources, 'load_config', return_value=self.settings).start()
        self.download = patch.object(tfds_sources.subprocess, 'run').start()

    def test_discovery_deduplicates_and_ignores_generated_and_symlink_sources(self):
        imported = self.corpus / 'imported'
        imported.mkdir()
        (imported / 'tfds').write_text('unsupported')
        (self.corpus / 'linked').symlink_to(imported, target_is_directory=True)
        self.assertEqual(tfds_sources.discover_sources(self.corpus), [self.source])

    def test_missing_cache_invokes_preparation_with_current_interpreter(self):
        target = self.root / 'new-cache'
        def prepare_cache(command, check):
            self.assertEqual(command[0], sys.executable)
            self.assertTrue(check)
            self.assertEqual(command[-2:], ['--out', str(target)])
            import shutil
            shutil.copytree(self.cache, target)
        self.download.side_effect = prepare_cache
        self.assertEqual(tfds_sources.ensure_dataset({**self.source, 'out': str(target)}), target)
        self.download.assert_called_once()

    def test_corrupt_binary_or_index_fails_without_silent_reuse(self):
        (self.cache / 'train.bin').write_bytes(b'broken')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            list(tfds_sources.documents([self.source]))
        self.download.assert_not_called()

    def test_official_holdout_and_local_copies_never_enter_training(self):
        (self.corpus / 'copy.txt').write_text(self.texts['test'] + '\n')
        with contextlib.redirect_stdout(io.StringIO()):
            prepare(self.corpus, self.out, external_documents=tfds_sources.documents([self.source]))
        self.assertIn(self.texts['train'].strip(), (self.out / 'train.bin').read_text())
        self.assertNotIn(self.texts['test'].strip(), (self.out / 'train.bin').read_text())
        self.assertNotIn(self.texts['validation'].strip(), (self.out / 'train.bin').read_text())
        self.assertEqual((self.out / 'valid.bin').read_text(), self.texts['validation'].strip())
        self.download.assert_not_called()

    def test_conflicting_local_training_history_is_rejected(self):
        (self.corpus / 'copy.txt').write_text(self.texts['test'] + '\n')
        with contextlib.redirect_stdout(io.StringIO()):
            prepare(self.corpus, self.out)
            with self.assertRaisesRegex(ValueError, 'overlaps official'):
                prepare(self.corpus, self.out, external_documents=tfds_sources.documents([self.source]))

    def test_training_handles_incremental_replay_and_reset_with_cached_tfds(self):
        args = SimpleNamespace(corpus=str(self.corpus), out=str(self.out), data=None,
                               reset=False, replay=False, resume=False, context=8, dim=8,
                               heads=2, layers=1, dropout=0.0, batch=1, steps=1,
                               threads=1, lr=0.001, seed=42)
        checkpoint, metadata = self.root / 'latest.keras', self.root / 'latest.json'
        with patch.object(train, 'CHECKPOINT', checkpoint), patch.object(train, 'METADATA', metadata), contextlib.redirect_stdout(io.StringIO()):
            train.run(args)
            self.assertEqual(json.loads(metadata.read_text())['step'], 1)
            args.data = None
            train.run(args)
            self.assertEqual(json.loads(metadata.read_text())['step'], 1)
            args.data, args.replay = None, True
            train.run(args)
            self.assertEqual(json.loads(metadata.read_text())['step'], 2)
            split_state = (self.out / 'split_state.json').read_text()
            args.data, args.replay, args.reset = None, False, True
            train.run(args)
            self.assertEqual(json.loads(metadata.read_text())['step'], 1)
            self.assertEqual((self.out / 'split_state.json').read_text(), split_state)
        self.download.assert_not_called()
        self.assertTrue((self.out / 'training_state.json').exists())

    def test_unsupported_identifier_fails_before_reset_changes_checkpoint(self):
        (self.corpus / 'tfds').write_text('../untrusted:1.0.0\n')
        checkpoint = self.root / 'checkpoint.keras'
        checkpoint.write_text('preserve')
        args = SimpleNamespace(corpus=str(self.corpus), out=str(self.out), reset=True)
        with patch.object(train, 'CHECKPOINT', checkpoint), self.assertRaisesRegex(ValueError, 'unsupported TFDS'):
            train.prepare_training_corpus(args)
        self.assertEqual(checkpoint.read_text(), 'preserve')
