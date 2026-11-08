"""Instruction corpus validation and response-only loss masking tests."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from instruction_train import read_examples, encode_example, prepare_examples, sample_indices
from config import load_config
from security_tools import planning_prompt


class InstructionTrainingTests(unittest.TestCase):
    def sample(self, split="train"):
        return {"tool": "nmap", "target": "192.168.60.10",
                "goal": "Inspect TCP port 8081.", "args": ["-sT", "-sV", "-p", "8081"],
                "reason": "Identify the service on the selected port.", "split": split}

    def write(self, root, items):
        path = root / "examples.jsonl"
        path.write_text("".join(json.dumps(item) + "\n" for item in items), encoding="utf-8")
        return path

    def test_reviewed_examples_validate_without_network(self):
        path = ROOT / "examples/planner_instructions.jsonl"
        examples = read_examples(path)
        self.assertGreaterEqual(len(examples), 6)
        self.assertTrue(any(item["split"] == "validation" for item in examples))
        self.assertTrue(any(item["split"] == "test" for item in examples))
        for item, (x, y, weights) in prepare_examples(path, 512):
            self.assertEqual(x.shape, (512,))
            self.assertEqual(y.shape, (512,))
            self.assertGreater(weights.sum(), 0)
            self.assertEqual(weights[-1], 0)

    def test_only_answer_bytes_receive_weight(self):
        item = self.sample()
        prompt, used = planning_prompt(item["tool"], item["target"], item["goal"], 512)
        self.assertEqual(used, item["goal"])
        x, y, weights = encode_example(item, 512)
        prefix_length = len(prompt.encode("utf-8"))
        self.assertEqual(weights[:prefix_length - 1].sum(), 0)
        self.assertEqual(weights[prefix_length - 1], 1)
        self.assertEqual(int(y[prefix_length - 1]), ord("{"))
        self.assertEqual(weights.sum(), len(json.dumps({
            "args": item["args"], "reason": item["reason"]}, separators=(",", ":")).encode()))

    def test_invalid_flags_and_duplicate_split_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            invalid = self.sample()
            invalid["args"] = ["--script", "vuln", "-p", "8081"]
            with self.assertRaisesRegex(ValueError, "Invalid instruction line"):
                read_examples(self.write(root, [invalid]))
            item = self.sample()
            with self.assertRaisesRegex(ValueError, "split leakage"):
                read_examples(self.write(root, [item, {**item, "split": "validation"}]))

    def test_oversized_training_record_fails_closed(self):
        item = self.sample()
        item["reason"] = "a" * 500
        with self.assertRaises(ValueError):
            encode_example(item, 512)

    def test_curriculum_and_balanced_sampling(self):
        import numpy as np
        records = [(self.sample(), None), ({**self.sample(), "tool": "nuclei"}, None)]
        first = sample_indices(records, 20, np.random.default_rng(7), 0, 90)
        second = sample_indices(records, 20, np.random.default_rng(7), 35, 90)
        mixed = sample_indices(records, 200, np.random.default_rng(7), 75, 90)
        self.assertEqual(set(first), {0})
        self.assertEqual(set(second), {1})
        self.assertEqual(set(mixed), {0, 1})
        self.assertEqual(list(mixed), list(sample_indices(records, 200, np.random.default_rng(7), 75, 90)))

    def test_no_scanner_execution(self):
        item = self.sample()
        with patch("security_tools.subprocess.Popen") as process:
            encode_example(item, 512)
            process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
