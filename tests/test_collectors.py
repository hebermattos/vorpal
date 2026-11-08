"""Bounded execution tests use owned Python children, never scanners or a LAN."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import collectors


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.output = self.root / 'output.xml'
        self.errors = self.root / 'stderr.log'

    def run_child(self, code, **limits):
        collectors.run([sys.executable, '-c', code], self.output, self.errors,
                       timeout=limits.get('timeout', 5), max_bytes=limits.get('max_bytes', 1024), cwd=self.root)

    def test_stdout_and_stderr_are_saved_separately(self):
        self.run_child('import sys; print("<nmaprun/>"); print("diagnostic",file=sys.stderr)')
        self.assertIn(b'<nmaprun/>', self.output.read_bytes())
        self.assertIn(b'diagnostic', self.errors.read_bytes())

    def test_nonzero_exit_preserves_diagnostics(self):
        with self.assertRaisesRegex(ValueError, 'status 2'):
            self.run_child('import sys; print("error",file=sys.stderr); sys.exit(2)')
        self.assertIn(b'error', self.errors.read_bytes())

    def test_deadline_terminates_an_owned_child(self):
        with self.assertRaisesRegex(ValueError, 'time limit'):
            self.run_child('import time; time.sleep(30)', timeout=.1)

    def test_output_limit_is_shared_by_both_streams(self):
        with self.assertRaisesRegex(ValueError, 'output limit'):
            self.run_child('import sys; print("x"*4096); print("y"*4096,file=sys.stderr)')
        self.assertLessEqual(self.output.stat().st_size + self.errors.stat().st_size, 1024)


if __name__ == '__main__':
    unittest.main()
