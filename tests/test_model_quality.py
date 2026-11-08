"""Offline regression tests for model planning quality metrics."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from model_quality import score_cases


class PlanningQualityTests(unittest.TestCase):
    def setUp(self):
        self.cases = json.loads((ROOT / "config/model_quality_cases.json").read_text(encoding="utf-8"))

    def test_valid_and_rejected_proposals(self):
        responses = {
            "nmap-web": json.dumps({"args": ["-sT", "-sV", "-p", "80,443"], "reason": "Web services"}),
            "nmap-ssh": json.dumps({"args": ["-sT", "-sV", "-p", "22"], "reason": "SSH"}),
            "invalid-public": json.dumps({"args": ["-sT", "-p", "53"], "reason": "No"}),
            "invalid-args": json.dumps({"args": ["--script", "vuln", "-p", "22"], "reason": "No"}),
        }
        metrics = score_cases(self.cases, responses)["metrics"]
        self.assertEqual(metrics["total"], 4)
        self.assertEqual(metrics["task_correct"], 2)
        self.assertEqual(metrics["refusal_correct"], 2)
        self.assertEqual(metrics["arguments_valid"], 2)

    def test_non_json_and_wrong_args_fail(self):
        responses = {c["id"]: "not-json" for c in self.cases}
        report = score_cases(self.cases, responses)
        self.assertEqual(report["metrics"]["json_valid"], 0)
        self.assertEqual(report["metrics"]["task_correct"], 0)

    def test_missing_response_rejected(self):
        with self.assertRaises(ValueError):
            score_cases(self.cases, {})


if __name__ == "__main__":
    unittest.main()
