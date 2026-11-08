"""Verify launcher binding and startup failures without starting a server."""

import contextlib
import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import run_ollama


class OllamaLauncherTests(unittest.TestCase):
    def test_uses_configured_loopback_endpoint_and_preserves_environment(self):
        with patch.object(run_ollama, 'load_config', return_value={'port': 8081, 'model': 'local'}), \
                patch.object(run_ollama.shutil, 'which', return_value='/usr/bin/ollama'), \
                patch.dict(os.environ, {'OLLAMA_HOST': '0.0.0.0:11434', 'VORPAL_TEST': 'preserved'}), \
                patch.object(run_ollama.os, 'execve') as execute, \
                contextlib.redirect_stdout(io.StringIO()):
            run_ollama.main([])
        executable, args, environment = execute.call_args.args
        self.assertEqual(executable, '/usr/bin/ollama')
        self.assertEqual(args, ['/usr/bin/ollama', 'serve'])
        self.assertEqual(environment['OLLAMA_HOST'], '127.0.0.1:8081')
        self.assertEqual(environment['VORPAL_TEST'], 'preserved')

    def test_missing_executable_reports_installation_hint(self):
        error = io.StringIO()
        with patch.object(run_ollama.shutil, 'which', return_value=None), \
                contextlib.redirect_stderr(error), self.assertRaises(SystemExit) as exit:
            run_ollama.main([])
        self.assertEqual(exit.exception.code, 2)
        self.assertIn('https://ollama.com/download', error.getvalue())


if __name__ == '__main__':
    unittest.main()
