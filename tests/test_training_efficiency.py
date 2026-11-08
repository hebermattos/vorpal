"""Memory use, sampling compatibility and resumed optimizer regressions."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import train
from evaluate import validation_loss
from model import build_model
from tensorflow import keras


class TrainingEfficiencyTests(unittest.TestCase):
    def test_readonly_mapping_handles_empty_and_binary_datasets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'train.bin'
            payload = bytes(range(256)) * 20
            path.write_bytes(payload)
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('full-file allocation')):
                data = train.load_bytes(path)
            self.assertIsInstance(data, np.memmap)
            self.assertFalse(data.flags.writeable)
            np.testing.assert_array_equal(data, np.frombuffer(payload, dtype=np.uint8))
            self.assertEqual(hashlib.sha256(data).hexdigest(), hashlib.sha256(payload).hexdigest())
            del data
            path.write_bytes(b'')
            self.assertEqual(len(train.load_bytes(path)), 0)

    def test_vectorized_windows_keep_old_samples_and_rng_state(self):
        data = np.arange(200, dtype=np.uint8)
        expected_rng, actual_rng = np.random.default_rng(42), np.random.default_rng(42)
        for _ in range(3):
            starts = expected_rng.integers(0, len(data) - 16, size=8)
            expected_x = np.stack([data[i:i+16] for i in starts]).astype('int32')
            expected_y = np.stack([data[i+1:i+17] for i in starts]).astype('int32')
            inputs, targets = train.make_batch(data, 16, 8, actual_rng)
            np.testing.assert_array_equal(inputs['tokens'], expected_x)
            np.testing.assert_array_equal(targets, expected_y)
            self.assertEqual(inputs['tokens'].dtype, np.int32)
        self.assertEqual(expected_rng.bit_generator.state, actual_rng.bit_generator.state)

    def test_single_window_dataset_is_valid_for_training_and_validation(self):
        data = np.arange(9, dtype=np.uint8)
        inputs, targets = train.make_batch(data, 8, 2, np.random.default_rng(42))
        np.testing.assert_array_equal(inputs['tokens'][0], data[:-1])
        np.testing.assert_array_equal(targets[1], data[1:])
        model = build_model(context=8, d_model=8, heads=2, layers=1, dropout=0)
        self.assertTrue(np.isfinite(validation_loss(model, data, samples=1, seed=42)))
        with self.assertRaisesRegex(ValueError, 'small'):
            train.make_batch(data[:-1], 8, 2, np.random.default_rng(42))

    def args(self, path):
        return SimpleNamespace(data=str(path), validation_data=str(path), prepared=True,
                               resume=False, context=8, dim=8, heads=2, layers=1,
                               dropout=0., batch=1, steps=1, threads=1, lr=.001, seed=42)

    def test_resumed_lr_keeps_optimizer_progress_and_nonfinite_loss_keeps_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            data = root / 'train.bin'
            data.write_bytes(bytes(range(256)) * 2)
            args = self.args(data)
            checkpoint, metadata = root / 'latest.keras', root / 'latest.json'
            with patch.object(train, 'CHECKPOINT', checkpoint), patch.object(train, 'METADATA', metadata):
                train.run(args)
                args.resume, args.lr = True, .0002
                train.run(args)
                model = keras.models.load_model(checkpoint)
                self.assertAlmostEqual(float(model.optimizer.learning_rate.numpy()), .0002)
                self.assertEqual(int(model.optimizer.iterations.numpy()), 2)
                self.assertEqual(json.loads(metadata.read_text())['step'], 2)
                checkpoint_before, metadata_before = checkpoint.read_bytes(), metadata.read_bytes()
                with patch.object(keras.Model, 'train_on_batch', return_value=float('nan')):
                    with self.assertRaisesRegex(FloatingPointError, 'step 3'):
                        train.run(args)
                self.assertEqual(checkpoint.read_bytes(), checkpoint_before)
                self.assertEqual(metadata.read_bytes(), metadata_before)

    def test_reported_loss_matches_each_batch_instead_of_running_average(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            data = root / 'train.bin'
            data.write_bytes(bytes(range(256)) * 2)
            args = self.args(data)
            args.steps = 2
            model = build_model(context=8, d_model=8, heads=2, layers=1, dropout=0)
            original = model.train_on_batch
            losses = []
            def measured(inputs, targets):
                logits = model(inputs, training=False)
                expected = float(np.mean(keras.losses.sparse_categorical_crossentropy(targets, logits, from_logits=True)))
                actual = float(original(inputs, targets))
                losses.append((expected, actual))
                return actual
            with patch.object(train, 'CHECKPOINT', root / 'latest.keras'), patch.object(train, 'METADATA', root / 'latest.json'), patch.object(train, 'build_model', return_value=model), patch.object(model, 'train_on_batch', side_effect=measured):
                train.run(args)
            self.assertEqual(len(losses), 2)
            for expected, actual in losses:
                self.assertAlmostEqual(actual, expected, places=5)

    def test_invalid_lr_fails_before_import_or_reset(self):
        for rate in (0., -1., float('nan'), float('inf')):
            args = self.args('missing.bin')
            args.prepared, args.reset, args.lr = False, True, rate
            args.corpus = 'missing-corpus'
            with patch.object(train, 'prepare_training_corpus') as prepare, self.assertRaisesRegex(ValueError, '--lr'):
                train.run(args)
            prepare.assert_not_called()
