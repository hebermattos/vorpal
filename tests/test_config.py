"""Configuration loading and command-line precedence regressions."""
import copy
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import config as config_module


class ConfigTests(unittest.TestCase):
    def tearDown(self):
        config_module.load_config.cache_clear()

    def test_tensorflow_environment_selects_cpu_and_keeps_errors_visible(self):
        with patch.dict(os.environ, {}, clear=True):
            config_module.configure_tensorflow_environment()
            self.assertEqual(os.environ["CUDA_VISIBLE_DEVICES"], "-1")
            self.assertEqual(os.environ["TF_CPP_MIN_LOG_LEVEL"], "2")

    def test_tensorflow_environment_preserves_explicit_debug_logging(self):
        with patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "0", "TF_CPP_MIN_LOG_LEVEL": "0"}, clear=True):
            config_module.configure_tensorflow_environment()
            self.assertEqual(os.environ["CUDA_VISIBLE_DEVICES"], "-1")
            self.assertEqual(os.environ["TF_CPP_MIN_LOG_LEVEL"], "0")

    def test_all_repository_files_load_outside_repository(self):
        config_module.load_config.cache_clear()
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                for path in config_module.CONFIG_DIR.glob("*.json"):
                    if path.stem != "schema":
                        with self.subTest(section=path.stem):
                            self.assertIsInstance(config_module.load_config(path.stem), dict)
            finally:
                os.chdir(previous)

    def test_modified_file_supplies_values(self):
        original = copy.deepcopy(config_module.load_config("local_vuln_scan"))
        original["policy"]["default_ports"] = [12345]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "schema.json").write_bytes((config_module.CONFIG_DIR / "schema.json").read_bytes())
            (root / "local_vuln_scan.json").write_text(json.dumps(original))
            config_module.load_config.cache_clear()
            with patch.object(config_module, "CONFIG_DIR", root):
                self.assertEqual(config_module.load_config("local_vuln_scan")["policy"]["default_ports"], [12345])

    def test_invalid_configuration_fails_with_file_and_field(self):
        original = copy.deepcopy(config_module.load_config("local_vuln_scan"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "schema.json").write_bytes((config_module.CONFIG_DIR / "schema.json").read_bytes())
            path = root / "local_vuln_scan.json"
            for field, value in (("timeout", -1), ("timeout", "slow"), ("authorized", True)):
                with self.subTest(field=field, value=value):
                    settings = copy.deepcopy(original)
                    settings["cli"][field] = value
                    path.write_text(json.dumps(settings))
                    config_module.load_config.cache_clear()
                    with patch.object(config_module, "CONFIG_DIR", root):
                        with self.assertRaisesRegex(config_module.ConfigError, "local_vuln_scan.json"):
                            config_module.load_config("local_vuln_scan")
            for ports in ([], [0], [65536], [True]):
                with self.subTest(ports=ports):
                    settings = copy.deepcopy(original)
                    settings["policy"]["default_ports"] = ports
                    path.write_text(json.dumps(settings))
                    config_module.load_config.cache_clear()
                    with patch.object(config_module, "CONFIG_DIR", root):
                        with self.assertRaisesRegex(config_module.ConfigError, "default_ports"):
                            config_module.load_config("local_vuln_scan")
            path.write_text("{invalid")
            config_module.load_config.cache_clear()
            with patch.object(config_module, "CONFIG_DIR", root):
                with self.assertRaisesRegex(config_module.ConfigError, "Cannot read configuration"):
                    config_module.load_config("local_vuln_scan")
            path.unlink()
            with patch.object(config_module, "CONFIG_DIR", root):
                with self.assertRaisesRegex(config_module.ConfigError, "Cannot read configuration"):
                    config_module.load_config("local_vuln_scan")

    def test_scan_cli_overrides_file_defaults_and_keeps_authorization_explicit(self):
        adapter = importlib.import_module("local_vuln_scan")
        module = sys.modules[adapter.main.__module__]
        settings = copy.deepcopy(module.SETTINGS)
        settings["cli"]["timeout"] = 2.5
        with patch.object(module, "SETTINGS", settings), patch.object(module, "run_scan") as scan:
            with patch.object(sys, "argv", ["vorpal.py", "192.168.0.10"]):
                module.main()
            self.assertEqual(scan.call_args.kwargs["timeout"], 2.5)
            self.assertFalse(scan.call_args.kwargs["authorized"])
            with patch.object(sys, "argv", ["vorpal.py", "192.168.0.10", "--timeout", "0.25", "--authorized"]):
                module.main()
            self.assertEqual(scan.call_args.kwargs["timeout"], 0.25)
            self.assertTrue(scan.call_args.kwargs["authorized"])

    def test_training_cli_overrides_file_defaults(self):
        module = importlib.import_module("train")
        settings = copy.deepcopy(module.SETTINGS)
        settings["cli"]["steps"] = 123
        with patch.object(module, "SETTINGS", settings), patch.object(module, "run_with_log") as run:
            with patch.object(sys, "argv", ["train.py"]):
                module.main()
            self.assertEqual(run.call_args.args[0].steps, 123)
            with patch.object(sys, "argv", ["train.py", "--steps", "2"]):
                module.main()
            self.assertEqual(run.call_args.args[0].steps, 2)
