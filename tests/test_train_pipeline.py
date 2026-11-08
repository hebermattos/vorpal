import io
import json
import hashlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from data import prepare
import train as train_module
from train import prepare_training_corpus, run, run_with_log, save_training_state


class TrainingPipelineTests(unittest.TestCase):
    def test_prepared_data_bypasses_import_and_corpus_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / 'train.bin'
            binary.write_bytes(bytes(range(256)) * 3)
            (root / 'valid.bin').write_bytes(bytes(range(256)) * 3)
            history = root / 'training_state.json'
            history.write_text('{"sentinel": true}')
            args = SimpleNamespace(prepared=True, data=str(binary), validation_data=None,
                                   corpus='missing-corpus', out=str(root), reset=False,
                                   replay=False, resume=False, context=8, dim=16, layers=1,
                                   heads=4, dropout=0.0, batch=1, steps=1, threads=1,
                                   lr=0.001, seed=42)
            with patch.object(train_module, 'CHECKPOINT', root / 'latest.keras'), \
                 patch.object(train_module, 'METADATA', root / 'latest.json'), \
                 patch('train.prepare_training_corpus') as prepare_corpus, \
                 patch('train.configure_cpu'):
                run(args)
            prepare_corpus.assert_not_called()
            self.assertEqual(history.read_text(), '{"sentinel": true}')
            self.assertTrue((root / 'latest.keras').is_file())
            self.assertFalse((root / 'metrics.jsonl').exists())

    def test_prepared_data_rejects_reset_replay_and_missing_data(self):
        for data, reset, replay in [(None, False, False), ('input.bin', True, False), ('input.bin', False, True)]:
            with self.subTest(data=data, reset=reset, replay=replay), self.assertRaises(ValueError):
                run(SimpleNamespace(prepared=True, data=data, reset=reset, replay=replay))

    def test_run_with_log_saves_and_tees_stdout_and_stderr(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            log_dir = Path(temporary_directory) / 'logs'
            args = SimpleNamespace(log_dir=str(log_dir))
            console_out = io.StringIO()
            console_err = io.StringIO()

            def fake_run(_args):
                print('step=10 loss=0.25')
                print('example warning', file=sys.stderr)

            with patch('train.run', side_effect=fake_run), \
                 redirect_stdout(console_out), redirect_stderr(console_err):
                log_path = run_with_log(args)

            contents = log_path.read_text(encoding='utf-8')
            self.assertTrue(log_path.is_file())
            self.assertIn('step=10 loss=0.25', contents)
            self.assertIn('example warning', contents)
            self.assertIn(str(log_path), console_out.getvalue())
            self.assertIn('step=10 loss=0.25', console_out.getvalue())
            self.assertIn('example warning', console_err.getvalue())

    def test_run_with_log_records_traceback_on_failure(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            args = SimpleNamespace(log_dir=temporary_directory)
            with patch('train.run', side_effect=RuntimeError('test training failure')), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    run_with_log(args)

            self.assertEqual(error.exception.code, 1)
            log_path = next(Path(temporary_directory).glob('train_*.log'))
            contents = log_path.read_text(encoding='utf-8')
            self.assertIn('Training run failed.', contents)
            self.assertIn('Traceback (most recent call last):', contents)
            self.assertIn('test training failure', contents)

    def test_training_imports_and_prepares_before_using_train_bin(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / 'corpus'
            output = Path(temporary_directory) / 'data'
            root.mkdir()
            (root / 'first.md').write_text(
                'Secure development validates untrusted input and tests access control. ' * 3,
                encoding='utf-8')
            args = SimpleNamespace(corpus=str(root), out=str(output), data=None, reset=False)
            with patch('train.import_directory') as import_directory:
                prepare_training_corpus(args)
            import_directory.assert_called_once_with(str(root), root / 'imported', force=False)
            self.assertEqual(args.data, str(output / 'train.bin'))
            manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(args.training_hashes, manifest['train_document_hashes'] + manifest['valid_document_hashes'])

    def test_training_uses_only_new_document_hashes(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / 'corpus'
            output = Path(temporary_directory) / 'data'
            root.mkdir()
            old_path = root / 'old.md'
            old_path.write_text('Previously trained safe development guidance. ' * 5, encoding='utf-8')
            first_args = SimpleNamespace(corpus=str(root), out=str(output), data=None, reset=False)
            with patch('train.import_directory'):
                prepare_training_corpus(first_args)
            save_training_state(output, first_args.training_hashes)

            new_path = root / 'new.md'
            new_path.write_text('New secure programming guidance for the next training run. ' * 5,
                                encoding='utf-8')
            second_args = SimpleNamespace(corpus=str(root), out=str(output), data=None, reset=False)
            with patch('train.import_directory'):
                prepare_training_corpus(second_args)

            manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['train_documents'], ['new.md'])
            self.assertEqual(second_args.training_hashes,
                             manifest['train_document_hashes'] + manifest['valid_document_hashes'])

    def test_no_new_training_documents_skips_model_creation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / 'corpus'
            output = Path(temporary_directory) / 'data'
            root.mkdir()
            content = 'Previously trained safe development guidance. ' * 5
            (root / 'used.md').write_text(content, encoding='utf-8')
            output.mkdir()
            state = output / 'training_state.json'
            state.write_text(json.dumps({
                'used_document_hashes': [hashlib.sha256(content.encode()).hexdigest()],
            }), encoding='utf-8')
            args = SimpleNamespace(corpus=str(root), out=str(output), data=None, reset=False,
                                   threads=2, batch=1, steps=1, context=8, dim=8,
                                   layers=1, heads=2, dropout=0.0, resume=False)
            with patch('train.import_directory'), patch('train.configure_cpu'), \
                 patch('train.build_model') as build_model:
                run(args)
            build_model.assert_not_called()
            self.assertEqual(args.training_hashes, [])

    def test_reset_forces_reimport_and_ignores_used_training_hashes(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / 'corpus'
            output = Path(temporary_directory) / 'data'
            root.mkdir()
            content = 'Previously trained safe development guidance. ' * 5
            (root / 'used.md').write_text(content, encoding='utf-8')
            output.mkdir()
            (output / 'training_state.json').write_text(json.dumps({
                'used_document_hashes': [hashlib.sha256(content.encode()).hexdigest()],
            }), encoding='utf-8')
            args = SimpleNamespace(corpus=str(root), out=str(output), data=None, reset=True)
            checkpoint = output / 'latest.keras'
            metadata = output / 'latest.json'
            checkpoint.write_text('old checkpoint', encoding='utf-8')
            metadata.write_text('old metadata', encoding='utf-8')
            with patch('train.import_directory') as import_directory, \
                 patch.object(train_module, 'CHECKPOINT', checkpoint), \
                 patch.object(train_module, 'METADATA', metadata):
                prepare_training_corpus(args)
            import_directory.assert_called_once_with(str(root), root / 'imported', force=True)
            self.assertEqual(len(args.training_hashes), 1)
            self.assertFalse((output / 'training_state.json').exists())
            self.assertFalse(checkpoint.exists())
            self.assertFalse(metadata.exists())

    def test_data_preparation_includes_files_larger_than_8_mib(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / 'corpus'
            output = Path(temporary_directory) / 'data'
            root.mkdir()
            content = 'Security guidance validates untrusted input before processing.\n' * 180000
            source = root / 'large.md'
            source.write_text(content, encoding='utf-8')
            self.assertGreater(source.stat().st_size, 8 * 1024 * 1024)
            prepare(root, output)
            manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
            self.assertGreater(manifest['train_bytes'], 8 * 1024 * 1024)

    def test_data_preparation_uses_shared_secret_filter(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / 'corpus'
            output = Path(temporary_directory) / 'data'
            root.mkdir()
            (root / 'safe.md').write_text(
                'Secure development requires validation, least privilege, and testing. ' * 3,
                encoding='utf-8')
            (root / 'unsafe.md').write_text(
                'Example configuration: password="example-sensitive-value-12345"\n' * 3,
                encoding='utf-8')
            prepare(root, output)
            manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['skipped_for_secrets'], ['unsafe.md'])


if __name__ == '__main__':
    unittest.main()