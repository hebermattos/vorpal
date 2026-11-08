import contextlib
import copy
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from config import load_config
import security_tools as tools
from security_tools import ToolPlanError


class SecurityToolTests(unittest.TestCase):
    def setUp(self):
        self.settings = copy.deepcopy(load_config('security_tools'))
        self.which = patch.object(tools.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name)
        self.which.start()
        self.addCleanup(self.which.stop)

    def plan(self, args=None):
        return tools.proposal_from_response('nmap', '127.0.0.1', 'Inspect a local web service',
            json.dumps({'args': args or ['-sT', '-sV', '-p', '80,443'], 'reason': 'Inspect selected web ports.'}), settings=self.settings)

    def test_targets_are_single_local_literals_and_nuclei_origins(self):
        self.assertEqual(tools.validate_target('nmap', '192.168.1.10'), '192.168.1.10')
        self.assertEqual(tools.validate_target('nuclei', 'http://127.0.0.1:8080/'), 'http://127.0.0.1:8080')
        cases = [('nmap', '8.8.8.8'), ('nmap', '192.168.1.0/24'), ('nmap', '127.0.0.1 127.0.0.2'),
                 ('nmap', 'localhost'), ('nmap', '0.0.0.0'), ('nmap', '::1'), ('nmap', '127.0.0.1;id'),
                 ('nuclei', 'https://example.com'), ('nuclei', 'ftp://127.0.0.1'),
                 ('nuclei', 'http://user:password@127.0.0.1'), ('nuclei', 'http://127.0.0.1/path'),
                 ('nuclei', 'http://127.0.0.1?url=https://example.com'), ('nuclei', 'http://127.0.0.1:65536')]
        for tool, target in cases:
            with self.subTest(tool=tool, target=target), self.assertRaises(ToolPlanError):
                tools.validate_target(tool, target)

    def test_nmap_arguments_are_selected_by_model_and_bounded_by_catalog(self):
        self.assertEqual(tools.validate_arguments('nmap', ['-sT', '-T2', '--top-ports', '20']), ['-sT', '-T', '2', '--top-ports', '20'])
        plan = self.plan()
        self.assertEqual(plan['args'], ['-sT', '-sV', '-p', '80,443'])
        self.assertEqual(plan['argv'][-1], '127.0.0.1')
        self.assertIn('--unprivileged', plan['argv'])
        self.assertIn('-n', plan['argv'])
        self.assertIn('--host-timeout', plan['argv'])

    def test_shell_scripts_extra_targets_and_output_files_are_rejected(self):
        cases = [[], ['-sC', '-p', '80'], ['--script', 'vuln', '-p', '80'], ['-A', '-p', '80'],
                 ['-p', '80', '8.8.8.8'], ['-p', '80;id'], ['-p', '0'], ['-p', '65536'],
                 ['-p', '80,80'], ['-p', '1-65535'], ['-p', '80', '-oA', '/tmp/output'],
                 ['-p', '80', '-F'], ['--top-ports', '1001'], ['-T', '5', '-p', '80'],
                 ['-p', '80', '-p', '443'], ['--version-light', '-p', '80'], ['sudo', 'nmap', '-p', '80']]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(ToolPlanError):
                tools.validate_arguments('nmap', args)

    def test_nuclei_uses_only_hash_pinned_reviewed_templates(self):
        command = tools.build_command('nuclei', 'http://127.0.0.1:8080', ['-t', 'server-header,missing-nosniff'])
        templates = command[command.index('-t') + 1]
        self.assertIn(str(tools.ROOT / 'tools/nuclei/templates/server-header.yaml'), templates)
        self.assertIn('-disable-redirects', command)
        self.assertIn('-no-interactsh', command)
        self.assertIn('-disable-update-check', command)
        self.assertEqual(command[-2:], ['-target', 'http://127.0.0.1:8080'])
        for args in (['-t', 'https://example.com/template.yaml'], ['-t', '/tmp/test.yaml'],
                     ['-t', '../../file'], ['-t', 'server-header', '-code'], ['-t', 'server-header', '-dashboard'],
                     ['-t', 'server-header,server-header'], ['-tags', 'cve']):
            with self.subTest(args=args), self.assertRaises(ToolPlanError):
                tools.validate_arguments('nuclei', args)
        bad = copy.deepcopy(self.settings)
        bad['templates'][0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ToolPlanError, 'changed'):
            tools.build_command('nuclei', 'http://127.0.0.1', ['-t', 'server-header'], settings=bad)

    def test_execution_policy_cannot_add_targets_or_drop_isolation(self):
        for tool, target, args in [('nmap', '127.0.0.1', ['-p', '80']), ('nuclei', 'http://127.0.0.1', ['-t', 'server-header'])]:
            for change in ('add', 'remove'):
                settings = copy.deepcopy(self.settings)
                if change == 'add': settings['tools'][tool]['required'] += ['8.8.8.8']
                else: settings['tools'][tool]['required'].pop(0)
                with self.subTest(tool=tool, change=change), self.assertRaises(ToolPlanError):
                    tools.build_command(tool, target, args, settings=settings)

    def test_invalid_model_json_has_no_fallback(self):
        for response in ('nmap -sT 127.0.0.1', '[]', '{"args": ["-p", "80"]}',
                         '{"args": ["--script", "vuln"], "reason": "try"}',
                         '{"args": ["-p", "80"], "reason": "", "target": "8.8.8.8"}'):
            with self.subTest(response=response), self.assertRaises(ToolPlanError):
                tools.proposal_from_response('nmap', '127.0.0.1', 'goal', response)

    def test_ollama_rag_planning_does_not_start_a_process(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'index.sqlite3'
            database.touch()
            client = Mock(return_value=json.dumps({'args': ['-p', '8080'], 'reason': 'Inspect the requested port.'}))
            docs = [{'source': 'nmap.txt', 'line': 1, 'sha256': 'a' * 64, 'text': 'Use -p for selected ports.'}]
            with patch('rag.search', return_value=docs), patch.object(tools.subprocess, 'Popen') as process:
                plan = tools.propose('nmap', '127.0.0.1', 'Inspect the local web port', database=database, client=client)
                self.assertEqual(plan['args'], ['-p', '8080'])
                self.assertEqual(plan['sources'][0]['source'], 'nmap.txt')
                self.assertIn('references', client.call_args.args[0][1]['content'])
                process.assert_not_called()
                client.return_value = 'incoherent output'
                with self.assertRaises(ToolPlanError):
                    tools.propose('nmap', '127.0.0.1', 'goal', database=database, client=client)
                process.assert_not_called()

    def test_execution_requires_authorization_and_unchanged_reviewed_digest(self):
        plan = self.plan()
        with self.assertRaises(ToolPlanError):
            tools.reviewed_command(plan, plan['digest'])
        self.assertEqual(tools.reviewed_command(plan, plan['digest'], authorized=True), plan['argv'])
        for field, value in [('target', '192.168.1.2'), ('args', ['-p', '22']), ('argv', ['echo', 'unsafe']), ('command', 'echo misleading-preview')]:
            changed = {**plan, field: value}
            with self.subTest(field=field), self.assertRaises(ToolPlanError):
                tools.reviewed_command(changed, plan['digest'], authorized=True)
        settings = copy.deepcopy(self.settings)
        settings['runtime']['timeout_seconds'] += 1
        with self.assertRaisesRegex(ToolPlanError, 'configuration changed'):
            tools.reviewed_command(plan, plan['digest'], authorized=True, settings=settings)

    def test_reports_and_execution_use_literal_argv_without_shell(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            plan = tools.write_report(self.plan(), 'plans', root=root)
            self.assertEqual(tools.read_plan(plan['id'], root=root)['digest'], plan['digest'])
            fake = SimpleNamespace(stdout=io.StringIO('tool result\n'), wait=Mock(return_value=0), terminate=Mock(), kill=Mock())
            with patch.object(tools.subprocess, 'Popen', return_value=fake) as process:
                report = tools.execute_plan(plan, plan['digest'], authorized=True, root=root)
            self.assertEqual(process.call_args.args[0], plan['argv'])
            self.assertFalse(process.call_args.kwargs.get('shell', False))
            self.assertEqual(report['status'], 'completed')
            self.assertIn('tool result', report['output'])
            with self.assertRaises(ToolPlanError):
                tools.read_plan('../../outside', root=root)

    def test_reviewed_corpus_examples_use_the_real_planner_format(self):
        paths = [path for tool in ('nmap', 'nuclei') for path in (tools.ROOT / 'corpus/tools' / tool / 'examples').glob('*.txt')]
        self.assertEqual(len(paths), 12)
        for path in paths:
            content = path.read_text()
            prompt, response = content.rsplit(self.settings['planner']['answer_prefix'], 1)
            request = json.loads(prompt[prompt.index('{"target":'):])
            tool = 'nuclei' if 'nuclei' in path.name else 'nmap'
            plan = tools.proposal_from_response(tool, request['target'], request['goal'], response)
            self.assertEqual(plan['status'], 'ready')

    def test_executor_timeout_stops_a_real_owned_child_process(self):
        settings = copy.deepcopy(self.settings)
        settings['runtime']['timeout_seconds'] = 1
        settings['runtime']['stop_grace_seconds'] = 1
        command = [sys.executable, '-u', '-c', 'import time; print("owned timeout fixture", flush=True); time.sleep(30)']
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), \
                patch.object(tools, 'build_command', return_value=command):
            plan = tools.proposal_from_response('nmap', '127.0.0.1', 'Test timeout handling',
                json.dumps({'args': ['-p', '80'], 'reason': 'Owned fixture.'}), settings=settings)
            plan = tools.write_report(plan, 'plans', settings=settings, root=Path(directory))
            report = tools.execute_plan(plan, plan['digest'], authorized=True, settings=settings, root=Path(directory))
            self.assertEqual(report['status'], 'timed_out')
            self.assertNotEqual(report['returncode'], 0)
            self.assertIn('owned timeout fixture', report['output'])
