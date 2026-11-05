"""CPU-only Keras model and checkpoint smoke tests."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import tensorflow as tf
from tensorflow import keras
from model import build_model, as_inputs
from generate import generate
from train import make_batch, run

class ModelTests(unittest.TestCase):
    def test_output_and_training_gradient(self):
        model = build_model(context=16, d_model=32, layers=1, heads=4, dropout=0)
        x = np.random.default_rng(2).integers(0, 256, (2, 10), dtype=np.int32)
        y = np.roll(x, -1, axis=1)
        with tf.GradientTape() as tape:
            logits = model(as_inputs(x), training=True)
            loss = tf.reduce_mean(
                keras.losses.sparse_categorical_crossentropy(y, logits, from_logits=True)
            )
        grads = tape.gradient(loss, model.trainable_variables)
        self.assertEqual(tuple(logits.shape), (2, 10, 256))
        self.assertTrue(np.isfinite(float(loss.numpy())))
        self.assertTrue(any(g is not None for g in grads))

    def test_causal_mask(self):
        model = build_model(context=16, d_model=32, layers=1, heads=4, dropout=0)
        x = np.array([[1, 2, 3, 4, 5]], dtype=np.int32)
        y = np.array([[1, 2, 3, 99, 98]], dtype=np.int32)
        a = model(as_inputs(x), training=False).numpy()
        b = model(as_inputs(y), training=False).numpy()
        np.testing.assert_allclose(a[:, :3], b[:, :3], atol=1e-5)

    def test_save_load_and_generation(self):
        model = build_model(context=12, d_model=16, layers=1, heads=4, dropout=0)
        x = np.array([[1, 2, 3, 4]], dtype=np.int32)
        a = model(as_inputs(x), training=False).numpy()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.keras"
            model.save(path)
            restored = keras.models.load_model(path, compile=False)
            b = restored(as_inputs(x), training=False).numpy()
            np.testing.assert_allclose(a, b, atol=1e-5)
            self.assertIsInstance(generate(restored, "test", max_new=3), str)

    def test_training_resume(self):
        from argparse import Namespace
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                Path("data").mkdir()
                Path("data/train.bin").write_bytes(bytes(range(256)) * 2)
                args = Namespace(context=12, dim=16, layers=1, heads=4,
                                 dropout=0.0, batch=2, steps=1, threads=2,
                                 lr=0.001, seed=42, data="data/train.bin",
                                 resume=False)
                run(args)
                self.assertTrue(Path("checkpoints/latest.keras").is_file())
                meta = json.loads(Path("checkpoints/latest.json").read_text())
                self.assertEqual(meta["step"], 1)
                args.resume = True
                args.steps = 2
                run(args)
                meta = json.loads(Path("checkpoints/latest.json").read_text())
                self.assertEqual(meta["step"], 2)
            finally:
                os.chdir(cwd)

    def test_batch_data(self):
        data = np.arange(200, dtype=np.uint8)
        inputs, targets = make_batch(data, 8, 2, np.random.default_rng(4))
        self.assertEqual(inputs["tokens"].shape, (2, 8))
        self.assertEqual(inputs["positions"].shape, (2, 8))
        np.testing.assert_array_equal(targets[:, :-1], inputs["tokens"][:, 1:])

if __name__ == "__main__":
    unittest.main()
