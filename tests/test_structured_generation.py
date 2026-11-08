"""Small deterministic generation fixture for the optional JSON constraint."""
import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from generate import generate


class JSONGenerationTests(unittest.TestCase):
    def test_json_mode_forces_object_start_and_rejects_controls(self):
        payload = b'{"args":[],"reason":"ok"}'
        class Model:
            index = 0
            def get_layer(self, name):
                return SimpleNamespace(input_dim=512)
            def __call__(self, inputs, training=False):
                logits = np.full((1, inputs["tokens"].shape[1], 256), -1e6, dtype=np.float32)
                logits[0, -1, payload[self.index]] = 1e6
                # Prefer invalid control bytes at each step: constraint excludes them.
                logits[0, -1, 1] = 1e7
                self.index += 1
                return SimpleNamespace(numpy=lambda: logits)
        model = Model()
        result = generate(model, "Answer: ", max_new=len(payload),
                          stop_on_json=True, json_mode=True)
        self.assertEqual(json.loads(result[len("Answer: "):]), {"args": [], "reason": "ok"})
        self.assertEqual(model.index, len(payload))

    def test_json_mode_requires_json_stopping(self):
        with self.assertRaises(ValueError):
            generate(None, "prompt", json_mode=True)


if __name__ == "__main__":
    unittest.main()
