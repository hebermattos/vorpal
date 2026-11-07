import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from code_quality import check_function
from config import load_config
from data import prepare
from import_dataset import collect_directory, import_one
import train


class DatasetLearningTests(unittest.TestCase):
    def test_resume_preserves_best_checkpoint_and_advances_sampling_state(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            binary = root / 'train.bin'
            binary.write_bytes(bytes(range(256)) * 2)
            args = SimpleNamespace(data=str(binary), validation_data=str(binary),
                                   context=8, dim=8, layers=1, heads=2, dropout=0.0,
                                   batch=1, steps=1, threads=2, lr=0.001, seed=42, resume=False)
            checkpoint, metadata = root / 'latest.keras', root / 'latest.json'
            with patch.object(train, 'CHECKPOINT', checkpoint), patch.object(train, 'METADATA', metadata), \
                    patch.object(train, 'validation_loss', side_effect=[1.0, 2.0]):
                train.run(args)
                before = json.loads(metadata.read_text())
                args.resume = True
                train.run(args)
            after = json.loads(metadata.read_text())
            self.assertEqual(after['step'], 2)
            self.assertNotEqual(before['rng_state'], after['rng_state'])
            best = json.loads((root / 'best.json').read_text())
            self.assertEqual(best['step'], 1)
            self.assertEqual(best['validation_loss'], 1.0)
            self.assertTrue((root / 'best.keras').is_file())
            self.assertEqual(len((root / 'metrics.jsonl').read_text().splitlines()), 2)
            (root / 'best.json').unlink()
            (root / 'best.keras').unlink()
            with patch.object(train, 'CHECKPOINT', checkpoint), patch.object(train, 'METADATA', metadata), \
                    patch.object(train, 'validation_loss', side_effect=[0.5, 1.5]):
                train.run(args)
            baseline = json.loads((root / 'best.json').read_text())
            self.assertEqual(baseline['step'], 2)
            self.assertEqual(baseline['validation_loss'], 0.5)
            self.assertEqual(json.loads(metadata.read_text())['step'], 3)

    def test_metadata_is_excluded_from_import_and_dataset(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory) / 'corpus'
            root.mkdir()
            (root / 'lesson.py').write_text('def example(value):\n    return value + 1\n' * 3)
            for name in ('import_state.json', 'manifest.json', 'training_state.json', 'split_state.json'):
                (root / name).write_text(json.dumps({'internal': 'metadata ' * 20}))
            sources, _ = collect_directory(root)
            self.assertEqual([source[0] for source in sources], ['lesson.py'])
            accepted, rejected = import_one('import_state.json', b'{}', 'test', Path(directory) / 'imported')
            self.assertEqual(accepted, [])
            self.assertIn('internal project metadata', rejected[0]['reason'])
            output = Path(directory) / 'data'
            prepare(root, output)
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertEqual(manifest['train_documents'], ['lesson.py'])
            self.assertEqual(len(manifest['skipped_internal_files']), 4)

    def test_split_is_stable_when_documents_are_added_renamed_or_excluded(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root, output = Path(directory) / 'corpus', Path(directory) / 'data'
            root.mkdir()
            for index in range(10):
                (root / f'{index}.md').write_text(f'Lesson {index}: validate input before processing. ' * 8)
            prepare(root, output)
            first = json.loads((output / 'manifest.json').read_text())
            (root / '0.md').rename(root / 'renamed.md')
            (root / 'new.md').write_text('A genuinely new programming exercise. ' * 10)
            prepare(root, output)
            second = json.loads((output / 'manifest.json').read_text())
            self.assertLessEqual(set(first['train_document_hashes']), set(second['train_document_hashes']))
            self.assertLessEqual(set(first['valid_document_hashes']), set(second['valid_document_hashes']))
            prepare(root, output, exclude_hashes=second['train_document_hashes'] + second['valid_document_hashes'])
            prepare(root, output)
            third = json.loads((output / 'manifest.json').read_text())
            self.assertEqual(second['train_document_hashes'], third['train_document_hashes'])
            self.assertEqual(second['valid_document_hashes'], third['valid_document_hashes'])

    def test_duplicate_import_newline_does_not_change_document_id(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root, output = Path(directory) / 'corpus', Path(directory) / 'data'
            root.mkdir()
            text = 'def validate(value):\n    """Return the supplied value unchanged."""\n    return value'
            (root / 'a.py').write_text(text)
            (root / 'b.txt').write_text(text + '\n')
            prepare(root, output)
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertEqual(manifest['train_document_hashes'], [hashlib.sha256(text.encode()).hexdigest()])
            self.assertEqual(manifest['skipped_duplicates'], ['b.txt'])

    def test_replay_ignores_consumed_hashes_and_preserves_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root, output = Path(directory) / 'corpus', Path(directory) / 'data'
            root.mkdir()
            text = 'Reviewed Python training content for a small local model. ' * 10
            (root / 'lesson.md').write_text(text)
            output.mkdir()
            state = output / 'training_state.json'
            state.write_text(json.dumps({'used_document_hashes': [hashlib.sha256(text.encode()).hexdigest()]}))
            checkpoint, metadata = output / 'latest.keras', output / 'latest.json'
            checkpoint.write_text('existing checkpoint')
            metadata.write_text('existing metadata')
            args = SimpleNamespace(corpus=str(root), out=str(output), data=None, reset=False, replay=True)
            with patch.object(train, 'CHECKPOINT', checkpoint), patch.object(train, 'METADATA', metadata), patch.object(train, 'import_directory') as importer:
                train.prepare_training_corpus(args)
            self.assertEqual(len(args.training_hashes), 1)
            self.assertEqual(checkpoint.read_text(), 'existing checkpoint')
            self.assertEqual(metadata.read_text(), 'existing metadata')
            self.assertTrue(state.is_file())
            self.assertFalse(importer.call_args.kwargs['force'])

    def test_replay_without_checkpoint_fails_before_import(self):
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(out=directory, reset=False, replay=True)
            with patch.object(train, 'CHECKPOINT', Path(directory) / 'absent.keras'), patch.object(train, 'import_directory') as importer:
                with self.assertRaisesRegex(ValueError, 'existing checkpoint'):
                    train.prepare_training_corpus(args)
                importer.assert_not_called()


class CodeQualityTests(unittest.TestCase):
    def test_reviewed_exercises_pass_behavior_checks(self):
        root = Path(__file__).resolve().parents[1]
        for task in load_config('benchmark')['tasks']:
            with self.subTest(name=task['name']):
                source = (root / 'corpus/python/examples' / (task['name'] + '.py')).read_text()
                result = check_function(source, task['name'], task['cases'])
                self.assertTrue(result['syntax_valid'])
                self.assertEqual(result['behavior_status'], 'passed')

    def test_invalid_and_wrong_code_are_distinguished(self):
        cases = [{'args': [443], 'expected': 443}]
        invalid = check_function('def validate_port(', 'validate_port', cases)
        self.assertFalse(invalid['syntax_valid'])
        wrong = check_function('def validate_port(port):\n    return 0', 'validate_port', cases)
        self.assertTrue(wrong['syntax_valid'])
        self.assertEqual(wrong['behavior_status'], 'failed')

    def test_unsafe_constructs_are_never_executed(self):
        examples = [
            'def f(x):\n    import os\n    return os.system(x)',
            'def f(x):\n    while True:\n        pass',
            'def f(x):\n    return __import__("os").system(x)',
            'def f(x):\n    return x ** x',
            'def f(x):\n    if False:\n        eval(x)\n    return x',
        ]
        for source in examples:
            with self.subTest(source=source):
                result = check_function(source, 'f', [{'args': [1], 'expected': 1}])
                self.assertEqual(result['behavior_status'], 'unsupported')
