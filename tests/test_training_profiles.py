"""Training subjects share one registry and retain document identities on moves."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from config import load_config
from data import prepare
from import_dataset import collect_directory
import train


class TrainingProfileTests(unittest.TestCase):
    def test_all_subjects_have_disjoint_corpus_roots_and_distinct_datasets(self):
        profiles = {profile['id']: profile for profile in load_config('web')['profiles']}
        self.assertTrue({'complete', 'security', 'python', 'nmap', 'nuclei'} <= profiles.keys())
        selected = [profiles[name] for name in ('security', 'python', 'nmap', 'nuclei')]
        self.assertEqual(len({profile['out'] for profile in selected}), 4)
        root = Path(__file__).resolve().parents[1]
        for profile in selected:
            self.assertTrue((root / profile['corpus']).is_dir())
        for a in selected:
            for b in selected:
                if a is not b:
                    self.assertFalse(Path(a['corpus']).is_relative_to(b['corpus']))

    def test_cli_selects_each_subject_without_starting_training(self):
        for profile in load_config('web')['profiles']:
            with self.subTest(profile=profile['id']), patch.object(train, 'run_with_log') as run:
                with patch.object(sys, 'argv', ['train.py', '--profile', profile['id'], '--replay', '--steps', '2']):
                    train.main()
                args = run.call_args.args[0]
                self.assertEqual(args.corpus, profile['corpus'])
                self.assertEqual(args.out, profile['out'])
                self.assertEqual(args.steps, 2)
                self.assertTrue(args.replay)

    def test_cli_explicit_paths_override_subject_defaults(self):
        with patch.object(train, 'run_with_log') as run, patch.object(sys, 'argv',
                ['train.py', '--profile', 'python', '--corpus', 'custom/corpus', '--out', 'custom/data']):
            train.main()
        self.assertEqual(run.call_args.args[0].corpus, 'custom/corpus')
        self.assertEqual(run.call_args.args[0].out, 'custom/data')

    def test_legacy_security_profile_resolves_to_english_paths(self):
        with patch.object(train, 'run_with_log') as run, patch.object(sys, 'argv',
                ['train.py', '--profile', 'seguranca']):
            train.main()
        self.assertEqual(run.call_args.args[0].profile, 'security')
        self.assertEqual(run.call_args.args[0].corpus, 'corpus/security')
        self.assertEqual(run.call_args.args[0].out, 'data/security')

    def test_unknown_subject_is_rejected_before_training(self):
        with patch.object(train, 'run_with_log') as run, patch.object(sys, 'argv', ['train.py', '--profile', 'unknown']), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit): train.main()
        run.assert_not_called()

    def test_complete_import_does_not_reimport_nested_generated_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'python/imported').mkdir(parents=True)
            (root / 'python/lesson.py').write_text('def lesson(value):\n    return value\n' * 3)
            (root / 'python/imported/cached.txt').write_text('Already extracted Python document for training. ' * 3)
            with contextlib.redirect_stdout(io.StringIO()): sources, _ = collect_directory(root)
            self.assertEqual([source[0] for source in sources], ['lesson.py'])
            with contextlib.redirect_stdout(io.StringIO()): prepare(root / 'python', root / 'dataset')
            manifest = json.loads((root / 'dataset/manifest.json').read_text())
            self.assertEqual(set(manifest['train_documents'] + manifest['valid_documents']),
                             {'lesson.py', 'imported/cached.txt'})

    def test_reordered_whitespace_duplicate_keeps_previous_document_hash(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root, output = Path(directory) / 'corpus', Path(directory) / 'data'
            root.mkdir()
            content = 'Reviewed programming material that is long enough for a training document.'
            original = root / 'a_original.md'
            original.write_text(content)
            (root / 'b_imported.txt').write_text(content + '\n')
            prepare(root, output)
            original_digest = hashlib.sha256(content.encode()).hexdigest()
            original.rename(root / 'z_moved.md')
            prepare(root, output)
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertEqual(manifest['train_document_hashes'], [original_digest])
            prepare(root, output, exclude_hashes=[original_digest])
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertEqual(manifest['train_document_hashes'], [])
            self.assertEqual(manifest['valid_document_hashes'], [])
