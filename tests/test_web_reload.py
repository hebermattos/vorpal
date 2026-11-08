"""Development reload excludes runtime files and defers active jobs."""
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from web import source_revision, watch_sources


class ReloadTests(unittest.TestCase):
    def test_revision_tracks_sources_and_ignores_runtime_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for directory in ('src', 'config', 'web', 'data'):
                (root / directory).mkdir()
            (root / 'src/example.py').write_text('x = 1')
            (root / 'config/example.json').write_text('{}')
            (root / 'web/app.js').write_text('initial')
            backend = source_revision(root)
            frontend = source_revision(root, frontend=True)
            (root / 'data/index.sqlite3').write_bytes(b'runtime')
            self.assertEqual(source_revision(root), backend)
            (root / 'web/app.js').write_text('changed frontend')
            self.assertEqual(source_revision(root), backend)
            self.assertNotEqual(source_revision(root, frontend=True), frontend)
            (root / 'src/added.py').write_text('x = 2')
            self.assertNotEqual(source_revision(root), backend)

    def test_restart_waits_for_job_and_closes_admission_before_shutdown(self):
        server = Mock()
        server.app.jobs.lock = threading.RLock()
        server.app.jobs.closed = False
        server.app.jobs.chat_active = False
        server.app.jobs.active.side_effect = [{'status': 'running'}, None]
        stopped, restart = Mock(), threading.Event()
        stopped.wait.return_value = False
        with patch('web.source_revision', side_effect=[['old'], ['new'], ['new']]):
            watch_sources(server, stopped, restart)
        self.assertTrue(restart.is_set())
        self.assertTrue(server.app.jobs.closed)
        self.assertEqual(server.app.jobs.active.call_count, 2)
        server.shutdown.assert_called_once_with()

    def test_restart_waits_for_chat_to_finish(self):
        server = Mock()
        server.app.jobs.lock = threading.RLock()
        server.app.jobs.closed = False
        server.app.jobs.chat_active = True
        server.app.jobs.active.return_value = None
        stopped, restart = Mock(), threading.Event()
        stopped.wait.return_value = False
        revisions = iter([['old'], ['new'], ['new']])
        calls = 0

        def revision(root):
            nonlocal calls
            calls += 1
            if calls == 3:
                server.shutdown.assert_not_called()
                self.assertFalse(server.app.jobs.closed)
                server.app.jobs.chat_active = False
            return next(revisions)

        with patch('web.source_revision', side_effect=revision):
            watch_sources(server, stopped, restart)
        self.assertTrue(restart.is_set())
        self.assertTrue(server.app.jobs.closed)
        server.shutdown.assert_called_once_with()
