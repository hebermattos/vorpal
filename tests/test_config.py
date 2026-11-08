"""Configuration loading and safety validation without inference dependencies."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from config import CONFIG_DIR, ConfigError, load_config, validate_config

class ConfigTests(unittest.TestCase):
    def test_all_repository_files_load_outside_repository(self):
        previous = Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.chdir(directory)
                for path in CONFIG_DIR.glob('*.json'):
                    if path.stem != 'schema':
                        self.assertIsInstance(load_config(path.stem), dict)
        finally:
            os.chdir(previous)

    def test_inference_requires_loopback(self):
        value = copy.deepcopy(load_config('scan_intelligence'))
        value['host'] = '0.0.0.0'
        with self.assertRaisesRegex(ConfigError, 'loopback'):
            validate_config('scan_intelligence', value)

    def test_web_paths_and_host_are_validated(self):
        for field, value in [('host', '0.0.0.0'), ('log_dir', '../outside')]:
            settings = copy.deepcopy(load_config('web'))
            settings['server' if field == 'host' else 'runtime'][field] = value
            with self.assertRaises(ConfigError):
                validate_config('web', settings)

    def test_unknown_fields_and_invalid_regex_fail(self):
        value = copy.deepcopy(load_config('corpus'))
        value['skip_name'] = '['
        with self.assertRaises(ConfigError):
            validate_config('corpus', value)
        with self.assertRaises(ConfigError):
            validate_config('assistant', dict(load_config('assistant'), unknown=True))

if __name__ == '__main__':
    unittest.main()
