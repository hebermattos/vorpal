"""Local HTTP contracts, job lifecycle and validation for the Vorpal dashboard."""
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
from config import ConfigError, load_config, validate_config
from web import Application, BusyError, JobManager, ROOT, Server
from security_tools import proposal_from_response, write_report


def wait_until(callback, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = callback()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError('Timed out waiting for local job')


class WebApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        shutil.copytree(ROOT / 'config', self.root / 'config')
        (self.root / 'checkpoints').mkdir()
        (self.root / 'checkpoints/latest.keras').write_text('fixture checkpoint')
        (self.root / 'checkpoints/latest.json').write_text(json.dumps({'step': 500, 'config': {'context': 256, 'layers': 3, 'd_model': 96}}))
        (self.root / 'corpus/pdfs').mkdir(parents=True)
        (self.root / 'corpus/pdfs/example.pdf').write_bytes(b'%PDF-fixture')
        (self.root / 'web').mkdir()
        (self.root / 'web/index.html').write_text('<html lang="en">Vorpal</html>')
        settings = copy.deepcopy(load_config('web'))
        self.app = Application(self.root, settings)
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.app.jobs.close)

    def test_generation_is_a_fixed_command_and_prompt_is_never_a_shell_command(self):
        prompt = 'def validate_port(\n$(touch /tmp/should-not-exist)'
        command = self.app.command('generate', {'prompt': prompt, 'max_new': 16, 'temperature': .3, 'threads': 2})
        self.assertEqual(command[:3], [sys.executable, '-u', str(self.root / 'src/generate.py')])
        self.assertEqual(command[command.index('--prompt') + 1], prompt)
        self.assertEqual(command[command.index('--max-new') + 1], '16')
        self.assertIn('--checkpoint', command)

    def test_security_question_uses_question_argument(self):
        question = 'Como proteger minhas contas?'
        command = self.app.command('generate', {'prompt': question, 'mode': 'security'})
        self.assertEqual(command[command.index('--question') + 1], question)
        self.assertNotIn('--prompt', command)
        with self.assertRaises(ValueError):
            self.app.command('generate', {'prompt': question, 'mode': 'invalid'})

    def test_legacy_security_profile_resolves_to_canonical_profile(self):
        self.assertEqual(self.app.profile('seguranca'), self.app.profile('security'))

    def test_train_replay_and_dataset_actions_preserve_existing_cli(self):
        command = self.app.command('train', {'profile': 'python', 'mode': 'replay', 'steps': 3, 'batch': 1, 'threads': 2})
        self.assertIn('--replay', command)
        self.assertNotIn('--reset', command)
        self.assertEqual(command[command.index('--corpus') + 1], 'corpus/python')
        self.assertEqual(command[command.index('--out') + 1], 'data/python')
        prepare = self.app.command('prepare', {'profile': 'complete'})
        self.assertTrue(prepare[2].endswith('/src/data.py'))
        importer = self.app.command('import', {'profile': 'complete'})
        self.assertIn('--directory', importer)

    def test_tool_planning_and_execution_are_separate_and_digest_bound(self):
        command = self.app.command('tool_plan', {'tool': 'nmap', 'target': '127.0.0.1', 'goal': 'Inspect local TCP ports', 'threads': 2})
        self.assertIn('plan', command)
        self.assertNotIn('--authorized', command)
        with patch('security_tools.shutil.which', return_value='/usr/bin/nmap'):
            plan = proposal_from_response('nmap', '127.0.0.1', 'goal', json.dumps({'args': ['-p', '80'], 'reason': 'Inspect web.'}))
            plan = write_report(plan, 'plans', root=self.root)
            params = {'plan_id': plan['id'], 'digest': plan['digest']}
            with self.assertRaises(ValueError):
                self.app.command('tool_run', params)
            params['authorized'] = True
            command = self.app.command('tool_run', params)
            self.assertIn('run', command)
            self.assertIn('--authorized', command)
            self.assertNotIn('shell', command)
            params['digest'] = '0' * 64
            with self.assertRaises(ValueError):
                self.app.command('tool_run', params)

    def test_rejects_unknown_actions_fields_invalid_ranges_and_missing_checkpoints(self):
        for action, params in [('shell', {}), ('train', {'mode': 'reset'}), ('train', {'steps': True}),
                               ('train', {'steps': 0}), ('train', {'threads': 10000}),
                               ('generate', {'temperature': 0}), ('generate', {'max_new': -1}),
                               ('generate', {'checkpoint': 'best'}), ('generate', {'shell': True}),
                               ('prepare', {'profile': '../outside'})]:
            with self.subTest(action=action, params=params), self.assertRaises(ValueError):
                self.app.command(action, params)
        with self.assertRaises(ValueError):
            self.app.path('../outside')

    def test_state_uses_real_artifacts_and_does_not_invent_metrics(self):
        state = self.app.state()
        self.assertEqual(state['metadata']['step'], 500)
        self.assertEqual(state['pdf_count'], 1)
        self.assertEqual(state['metrics'], [])
        self.assertIsNone(state['benchmark'])
        self.assertIsNone(state['best'])
        self.assertIsNone(state['profiles'][0]['manifest'])

    def test_configuration_is_validated_before_writing_and_restart_is_reported(self):
        old = self.app.configuration('train')
        invalid = copy.deepcopy(old)
        invalid['cli']['heads'] = 5
        with self.assertRaises(ConfigError):
            self.app.save_configuration('train', invalid)
        self.assertEqual(self.app.configuration('train'), old)
        changed = copy.deepcopy(old)
        changed['cli']['steps'] = 123
        result = self.app.save_configuration('train', changed)
        self.assertFalse(result['restart_required'])
        self.assertEqual(self.app.configuration('train')['cli']['steps'], 123)
        web = self.app.configuration('web')
        self.assertTrue(self.app.save_configuration('web', web)['restart_required'])

    def test_configuration_and_second_job_are_blocked_during_an_active_job(self):
        job = self.app.jobs.start('train', [sys.executable, '-u', '-c', 'import time; print("ready", flush=True); time.sleep(30)'])
        wait_until(lambda: 'ready' in self.app.jobs.get(job['id'])['output'])
        with self.assertRaises(BusyError):
            self.app.jobs.start('generate', [sys.executable, '-c', 'print(1)'])
        with self.assertRaises(BusyError):
            self.app.save_configuration('train', self.app.configuration('train'))
        self.app.jobs.cancel(job['id'])
        done = wait_until(lambda: self.app.jobs.get(job['id']) if self.app.jobs.get(job['id'])['status'] != 'running' else None)
        self.assertEqual(done['status'], 'cancelled')

    def test_job_logs_are_bounded_persistent_and_failure_is_visible(self):
        self.app.jobs.settings['max_output_chars'] = 20
        job = self.app.jobs.start('generate', [sys.executable, '-u', '-c', 'print("x" * 200); raise SystemExit(3)'])
        done = wait_until(lambda: self.app.jobs.get(job['id']) if self.app.jobs.get(job['id'])['status'] != 'running' else None)
        self.assertEqual(done['status'], 'failed')
        self.assertEqual(done['returncode'], 3)
        self.assertLessEqual(len(done['output']), 20)
        log = self.app.jobs.directory / (job['id'] + '.log')
        self.assertGreater(log.stat().st_size, 200)
        restored = JobManager(self.root, self.app.jobs.settings)
        self.addCleanup(restored.close)
        self.assertEqual(restored.get(job['id'])['status'], 'failed')
        self.assertEqual(restored.get(job['id'])['output'], done['output'])

    def test_evaluation_result_is_parsed_from_completed_job(self):
        job = self.app.jobs.start('evaluate', [sys.executable, '-u', '-c', 'print("Validation loss=2.6599; byte-level perplexity=14.29")'])
        wait_until(lambda: self.app.jobs.get(job['id'])['status'] == 'completed')
        evaluation = self.app.state()['evaluation']
        self.assertEqual(evaluation['loss'], 2.6599)
        self.assertEqual(evaluation['perplexity'], 14.29)


class WebHTTPTests(unittest.TestCase):
    def setUp(self):
        WebApplicationTests.setUp(self)
        self.server = Server(('127.0.0.1', 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=3)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.read()
        status = response.status
        response_headers = dict(response.getheaders())
        connection.close()
        return status, result, response_headers

    def test_page_state_and_pdf_are_served_and_paths_are_restricted(self):
        status, payload, headers = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'Vorpal', payload)
        self.assertIn("script-src 'self'", headers['Content-Security-Policy'])
        self.assertEqual(self.request('GET', '/api/state')[0], 200)
        self.assertEqual(self.request('GET', '/files?path=corpus/pdfs/example.pdf')[0], 200)
        self.assertEqual(self.request('GET', '/files?path=config/train.json')[0], 404)
        self.assertEqual(self.request('GET', '/files?path=../outside.pdf')[0], 400)
        self.assertEqual(self.request('GET', '/../../etc/passwd')[0], 404)
        self.assertEqual(self.request('GET', '/api/config?name=schema')[0], 400)

    def test_cross_origin_dns_rebinding_and_missing_token_are_rejected(self):
        self.assertEqual(self.request('GET', '/api/state', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('GET', '/api/state', headers={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('POST', '/api/jobs', '{}', {'Content-Type': 'application/json'})[0], 403)
        headers = {'Content-Type': 'application/json', 'X-Vorpal-Token': self.app.token, 'Origin': 'https://evil.example'}
        self.assertEqual(self.request('POST', '/api/jobs', '{}', headers)[0], 403)

    def test_mutations_validate_json_and_ranges_before_starting_processes(self):
        headers = {'Content-Type': 'application/json', 'X-Vorpal-Token': self.app.token}
        self.assertEqual(self.request('POST', '/api/jobs', 'invalid json', headers)[0], 400)
        self.assertEqual(self.request('POST', '/api/jobs', '[]', headers)[0], 400)
        payload = json.dumps({'action': 'train', 'params': {'steps': -1}})
        self.assertEqual(self.request('POST', '/api/jobs', payload, headers)[0], 400)
        self.assertEqual(self.app.jobs.snapshot(), [])
        self.assertEqual(self.request('POST', '/api/jobs', '{}', {'X-Vorpal-Token': self.app.token})[0], 400)

    def test_configuration_can_be_saved_over_http_without_bypassing_validation(self):
        headers = {'Content-Type': 'application/json', 'X-Vorpal-Token': self.app.token}
        value = self.app.configuration('train')
        value['cli']['steps'] = 123
        body = json.dumps({'name': 'train', 'value': value})
        self.assertEqual(self.request('POST', '/api/config', body, headers)[0], 200)
        status, payload, _ = self.request('GET', '/api/config?name=train')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)['value']['cli']['steps'], 123)
        value['cli']['heads'] = 5
        body = json.dumps({'name': 'train', 'value': value})
        self.assertEqual(self.request('POST', '/api/config', body, headers)[0], 400)
        self.assertEqual(self.app.configuration('train')['cli']['heads'], 4)

    def test_job_can_be_started_and_observed_over_http(self):
        script = self.root / 'src/data.py'
        script.parent.mkdir()
        script.write_text('print("prepared fixture dataset")\n')
        headers = {'Content-Type': 'application/json', 'X-Vorpal-Token': self.app.token}
        status, payload, _ = self.request('POST', '/api/jobs', json.dumps({'action': 'prepare', 'params': {'profile': 'complete'}}), headers)
        self.assertEqual(status, 202)
        identifier = json.loads(payload)['id']
        wait_until(lambda: self.app.jobs.get(identifier)['status'] == 'completed')
        status, payload, _ = self.request('GET', '/api/jobs/' + identifier)
        self.assertEqual(status, 200)
        self.assertIn('prepared fixture dataset', json.loads(payload)['output'])


class WebAssetsTests(unittest.TestCase):
    def test_markup_labels_and_script_targets_match_real_elements(self):
        class Markup(HTMLParser):
            def __init__(self):
                super().__init__()
                self.ids = []
                self.labels = []
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if 'id' in attrs:
                    self.ids.append(attrs['id'])
                if tag == 'label' and 'for' in attrs:
                    self.labels.append(attrs['for'])
        markup = Markup()
        markup.feed((ROOT / 'web/index.html').read_text())
        self.assertEqual(len(markup.ids), len(set(markup.ids)))
        self.assertTrue(set(markup.labels) <= set(markup.ids))
        javascript = (ROOT / 'web/app.js').read_text()
        targets = set(re.findall(r"\$\(['\"]#([a-z][a-z-]+)['\"]\)", javascript))
        self.assertTrue(targets <= set(markup.ids), targets - set(markup.ids))

    def test_web_configuration_cannot_expose_server_on_the_network(self):
        settings = copy.deepcopy(load_config('web'))
        settings['server']['host'] = '0.0.0.0'
        with self.assertRaisesRegex(ConfigError, 'loopback'):
            validate_config('web', settings)
        settings = copy.deepcopy(load_config('web'))
        settings['profiles'][0]['out'] = '../outside'
        with self.assertRaisesRegex(ConfigError, 'inside the project'):
            validate_config('web', settings)
