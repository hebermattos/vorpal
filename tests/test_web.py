"""Assistant dashboard HTTP, job, configuration and asset contracts."""
import copy
from html.parser import HTMLParser
import http.client
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from config import load_config
from web import Application, BusyError, JobManager, ROOT, Server

class WebApplicationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        shutil.copytree(ROOT / 'config', self.root / 'config')
        (self.root / 'web').mkdir()
        (self.root / 'web/index.html').write_text('<html lang="en">Vorpal</html>')
        self.app = Application(self.root, copy.deepcopy(load_config('web')))
        self.addCleanup(self.app.jobs.close)

    def test_ip_analysis_authorization_and_scope_without_local_weights(self):
        with self.assertRaises(ValueError): self.app.command('analyze_ip', {'target': '127.0.0.1'})
        command = self.app.command('analyze_ip', {'target': '127.0.0.1', 'authorized': True, 'ports': '22,80'})
        self.assertIn('analyze', command)
        self.assertIn('--authorized', command)
        nmap = self.app.command('analyze_ip', {'target': '127.0.0.1', 'authorized': True, 'tool': 'nmap'})
        self.assertEqual(nmap[nmap.index('--tool') + 1], 'nmap')
        reviewed = self.app.command('analyze_ip', {'target': '127.0.0.1', 'authorized': True, 'tool': 'nmap',
                                    'goal': 'Identify SSH', 'plan_id': 'a' * 24, 'digest': 'b' * 64})
        self.assertEqual(reviewed[reviewed.index('--goal') + 1], 'Identify SSH')
        self.assertEqual(reviewed[reviewed.index('--plan-id') + 1], 'a' * 24)
        with self.assertRaises(ValueError): self.app.command('analyze_ip', {'target': '127.0.0.1', 'authorized': True, 'tool': 'shell'})
        with self.assertRaises(ValueError): self.app.command('analyze_ip', {'target': '8.8.8.8', 'authorized': True})
        with self.assertRaises(ValueError): self.app.command('shell', {})
        with self.assertRaises(ValueError): self.app.command('analyze_ip', {'target': '127.0.0.1', 'authorized': True, 'shell': True})

    def test_knowledge_and_tool_planning_commands(self):
        self.assertEqual(self.app.command('knowledge_update', {'import_sources': True})[-1], '--import-sources')
        command = self.app.command('tool_plan', {'tool': 'nmap', 'target': '127.0.0.1', 'goal': 'Inspect SSH'})
        self.assertEqual(command[-1], 'Inspect SSH')
        with self.assertRaises(ValueError): self.app.command('knowledge_update', {'import_sources': 'yes'})

    def test_chat_blocks_jobs_and_clears_busy_state_on_error(self):
        def fail(*args, **kwargs):
            self.assertTrue(self.app.jobs.chat_active)
            with self.assertRaises(BusyError): self.app.start('knowledge_update', {})
            raise ValueError('Local model unavailable')
        with patch('assistant.chat', side_effect=fail), self.assertRaisesRegex(ValueError, 'unavailable'):
            self.app.chat({'message': 'SSH security'})
        self.assertFalse(self.app.jobs.chat_active)

    def test_state_and_configuration_use_live_settings(self):
        state = self.app.state()
        self.assertEqual(state['inference']['model'], load_config('scan_intelligence')['model'])
        settings = self.app.configuration('scan_intelligence')
        settings['port'] = 11435
        self.app.save_configuration('scan_intelligence', settings)
        self.assertEqual(self.app.configuration('scan_intelligence')['port'], 11435)
        with self.assertRaises(ValueError): self.app.configuration('../outside')
        self.app.jobs.chat_active = True
        with self.assertRaises(BusyError): self.app.save_configuration('scan_intelligence', settings)
        self.app.jobs.chat_active = False

    def test_background_job_logs_are_persistent(self):
        job = self.app.jobs.start('fixture', [sys.executable, '-c', 'print("fixture completed")'])
        deadline = time.monotonic() + 5
        while self.app.jobs.get(job['id'])['status'] == 'running' and time.monotonic() < deadline: time.sleep(.01)
        result = self.app.jobs.get(job['id'])
        self.assertEqual(result['status'], 'completed')
        self.assertIn('fixture completed', result['output'])
        self.assertTrue((self.app.jobs.directory / (job['id'] + '.log')).is_file())

class WebHTTPTests(WebApplicationTests):
    def setUp(self):
        super().setUp()
        self.server = Server(('127.0.0.1', 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=3)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse(); data = response.read(); status = response.status
        connection.close(); return status, data

    def test_page_and_state_and_security_headers(self):
        self.assertEqual(self.request('GET', '/')[0], 200)
        self.assertEqual(self.request('GET', '/api/state')[0], 200)
        self.assertEqual(self.request('GET', '/api/state', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('GET', '/api/state', headers={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('GET', '/api/config?name=../outside')[0], 400)

    def test_chat_token_and_parameters_and_shared_backend(self):
        body = json.dumps({'message': 'SSH security'})
        self.assertEqual(self.request('POST', '/api/chat', body, {'Content-Type': 'application/json'})[0], 403)
        headers = {'Content-Type': 'application/json', 'X-Vorpal-Token': self.app.token}
        with patch('assistant.chat', return_value={'answer': 'Use keys [1].', 'sources': [], 'session_id': 'a' * 24}) as chat:
            status, payload = self.request('POST', '/api/chat', body, headers)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(payload)['answer'], 'Use keys [1].')
            self.assertEqual(chat.call_args.kwargs['root'], self.root)
        self.assertEqual(self.request('POST', '/api/chat', '{"message":"SSH","shell":true}', headers)[0], 400)
        self.assertEqual(self.request('POST', '/api/jobs', '{"action":"shell"}', headers)[0], 400)

class WebAssetsTests(unittest.TestCase):
    def test_markup_and_script_targets_match(self):
        class Markup(HTMLParser):
            def __init__(self): super().__init__(); self.ids=[]; self.labels=[]
            def handle_starttag(self, tag, attrs):
                attrs=dict(attrs)
                if 'id' in attrs: self.ids.append(attrs['id'])
                if tag=='label' and 'for' in attrs: self.labels.append(attrs['for'])
        markup=Markup(); markup.feed((ROOT/'web/index.html').read_text())
        self.assertEqual(len(markup.ids),len(set(markup.ids)))
        self.assertTrue(set(markup.labels)<=set(markup.ids))
        targets=set(re.findall(r"\$\(['\"]#([a-z][a-z-]+)['\"]\)",(ROOT/'web/app.js').read_text()))
        self.assertTrue(targets<=set(markup.ids),targets-set(markup.ids))

if __name__ == '__main__': unittest.main()
