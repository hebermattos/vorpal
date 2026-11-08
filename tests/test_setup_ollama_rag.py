"""Exercise setup orchestration with fake tools; never download or start services."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('bash'), 'Bash is required')
class SetupTests(unittest.TestCase):
    def test_existing_server_setup_and_import_flags(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'scripts').mkdir()
            shutil.copy(ROOT / 'scripts/setup_ollama_rag.sh', root / 'scripts/setup.sh')
            tools = root / '.venv/bin'
            tools.mkdir(parents=True)
            python = tools / 'python'
            python.write_text('''#!/usr/bin/env python3
import os, sys
with open(os.environ['CALL_LOG'], 'a') as log:
    log.write('python ' + ' '.join(sys.argv[1:]) + '\\n')
if sys.argv[1:] == ['-']:
    source = sys.stdin.read()
    if "setup = load_config" in source:
        print('fixture:3b\\nlocal\\n8081\\n1')
''')
            ollama = tools / 'ollama'
            ollama.write_text('''#!/usr/bin/env bash
echo "ollama $* host=$OLLAMA_HOST" >> "$CALL_LOG"
if [[ $1 == show ]]; then exit 1; fi
''')
            python.chmod(0o755)
            ollama.chmod(0o755)
            calls = root / 'calls.log'
            env = dict(os.environ, CALL_LOG=str(calls), PATH=str(tools) + os.pathsep + os.environ['PATH'])
            result = subprocess.run(['bash', str(root / 'scripts/setup.sh'), '--import-sources',
                                     '--database', 'data/rag/new.sqlite3'], env=env,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            log = calls.read_text()
            self.assertIn('ollama pull fixture:3b host=127.0.0.1:8081', log)
            self.assertIn('ollama cp fixture:3b local host=127.0.0.1:8081', log)
            self.assertIn('vorpal.py knowledge update --import-sources --database data/rag/new.sqlite3', log)
            self.assertNotIn('ollama serve', log)
            self.assertNotIn('sentence-transformers', log)

    def test_unknown_option_fails_before_setup(self):
        result = subprocess.run(['bash', str(ROOT / 'scripts/setup_ollama_rag.sh'), '--unknown'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Unknown argument', result.stderr)


if __name__ == '__main__':
    unittest.main()
