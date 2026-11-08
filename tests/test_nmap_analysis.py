"""Selected Nmap collection is validated, logged and analyzed without executing it."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import assistant
import rag

XML = b'''<?xml version="1.0"?><!DOCTYPE nmaprun>
<nmaprun><host><address addr="127.0.0.1" addrtype="ipv4"/><ports>
<port protocol="tcp" portid="22"><state state="open"/><service name="ssh" product="FixtureSSH" version="1.0"/></port>
</ports></host></nmaprun>'''


class NmapAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        corpus = self.root / 'corpus'; corpus.mkdir()
        (corpus / 'security.txt').write_text('Network security hardening firewall SSH access requires reviewed configuration and key authentication.')
        self.db = self.root / 'index.sqlite3'; rag.index(corpus, self.db)

    def analyze_reviewed(self, target, **kwargs):
        if kwargs.get('tool') != 'nmap' or kwargs.get('authorized') is not True:
            return assistant.analyze_ip(target, **kwargs)
        selected = assistant.ports_from_text(kwargs.get('ports'))
        with patch('security_tools.shutil.which', return_value='/usr/bin/nmap'):
            plan = assistant.security_tools.proposal_from_response(
                'nmap', target, 'Fixture reviewed goal', json.dumps({'args': [*assistant.load_config('assistant')['nmap_required_flags'], '-p', ','.join(map(str, selected))],
                                                                 'reason': 'Review the selected ports.'}))
            plan = assistant.security_tools.write_report(plan, 'plans', root=self.root)
        return assistant.analyze_ip(target, plan_id=plan['id'], digest=plan['digest'], **kwargs)

    def model(self, *args):
        return json.dumps({'answer': 'Review SSH access [1].', 'sources': [1]})

    def test_nmap_generates_a_model_command_for_review_without_execution(self):
        def planner(messages, settings):
            payload = json.loads(messages[-1]['content'])
            self.assertEqual(payload['constraints']['ports'], [22])
            self.assertTrue(payload['references'])
            return json.dumps({'args': ['-sT', '-sV', '--version-light', '-p', '22'],
                               'reason': 'Use light service detection to identify SSH.'})
        with patch('security_tools.shutil.which', return_value='/usr/bin/nmap'), patch('collectors.run') as run:
            proposal = assistant.analyze_ip('127.0.0.1', authorized=True, tool='nmap', ports='22',
                                             goal='Identify SSH', root=self.root, database=self.db, client=planner)
        run.assert_not_called()
        self.assertEqual(proposal['status'], 'command_review_required')
        self.assertIn('--version-light', proposal['argv'])
        self.assertIn('reason', proposal)
        self.assertTrue(proposal['plan_id'])

    def test_default_ports_are_bound_when_model_omits_port_selection(self):
        with patch('security_tools.shutil.which', return_value='/usr/bin/nmap'), patch('collectors.run') as run:
            result = assistant.analyze_ip('192.168.0.109', authorized=True, tool='nmap',
                         goal='Identify services and versions with low-impact probes', root=self.root, database=self.db,
                         client=lambda *args: json.dumps({'args': ['-sT', '-sV', '--version-light'],
                                                         'reason': 'Use light version detection.'}))
        run.assert_not_called()
        ports = result['argv'][result['argv'].index('-p') + 1]
        self.assertEqual([int(p) for p in ports.split(',')], assistant.ports_from_text())
        self.assertEqual(result['planning_attempts'], 1)
        self.assertEqual(result['argv'][-1], '192.168.0.109')

    def test_planning_schema_excludes_literal_placeholder_arguments(self):
        seen = []
        def model(messages, settings):
            seen.append((messages, settings))
            return json.dumps({'args': ['-sT', '-sV', '--version-light'], 'reason': 'Identify selected services.'})
        with patch('security_tools.shutil.which', return_value='/usr/bin/nmap'), patch('collectors.run') as run:
            result = assistant.analyze_ip('127.0.0.1', authorized=True, tool='nmap', ports='22',
                                          root=self.root, database=self.db, client=model)
        run.assert_not_called()
        schema = seen[0][1]['response_schema']
        tokens = schema['properties']['args']['items']['enum']
        self.assertTrue({'-sT', '-sV', '--version-light'} <= set(tokens))
        self.assertFalse({'flag', 'value', '-F', '--top-ports', '-p'} & set(tokens))
        payload = json.loads(seen[0][0][-1]['content'])
        self.assertEqual(payload['output_schema'], schema)
        self.assertEqual(result['status'], 'command_review_required')
        self.assertNotIn('"flag","value"', seen[0][0][0]['content'])

    def test_literal_placeholder_response_is_rejected_then_corrected_by_model(self):
        calls = []
        def model(messages, settings):
            calls.append(json.loads(messages[-1]['content']))
            if len(calls) == 1:
                return '{"args":["flag","value"],"reason":"why"}'
            return json.dumps({'args': ['-sT', '-sV', '--version-light'], 'reason': 'Identify selected services.'})
        with patch('security_tools.shutil.which', return_value='/usr/bin/nmap'), patch('collectors.run') as run:
            result = assistant.analyze_ip('192.168.0.109', authorized=True, tool='nmap',
                                          root=self.root, database=self.db, client=model)
        run.assert_not_called()
        self.assertIn('flag', calls[1]['previous_rejection'])
        self.assertEqual(result['planning_attempts'], 2)
        self.assertNotIn('flag', result['argv'])

    def test_conflicting_discovery_modes_are_rejected_with_saved_diagnostics(self):
        response = json.dumps({'args': ['-sT', '-sV', '--version-light', '-F', '-p', '22'],
                               'reason': 'Invalid mixed port selection.'})
        with patch('security_tools.shutil.which', return_value='/usr/bin/nmap'), patch('collectors.run') as run:
            with self.assertRaisesRegex(ValueError, 'Diagnostics:') as error:
                assistant.analyze_ip('127.0.0.1', authorized=True, tool='nmap', ports='22',
                                     root=self.root, database=self.db, client=lambda *args: response)
        run.assert_not_called()
        path = Path(str(error.exception).split('Diagnostics: ', 1)[1])
        record = json.loads(path.read_text())
        self.assertEqual(record['status'], 'rejected')
        self.assertEqual(len(record['rejections']), assistant.load_config('assistant')['planning_attempts'])
        self.assertEqual(record['rejections'][0]['model_response'], response)

    def test_model_cannot_broaden_selected_ports(self):
        with patch('security_tools.shutil.which', return_value='/usr/bin/nmap'), patch('collectors.run') as run:
            with self.assertRaisesRegex(ValueError, 'port scope'):
                assistant.analyze_ip('127.0.0.1', authorized=True, tool='nmap', ports='22',
                                     root=self.root, database=self.db,
                                     client=lambda *args: json.dumps({'args': ['-p', '22,80'], 'reason': 'Broader scan.'}))
        run.assert_not_called()

    def test_reviewed_generated_command_runs_then_analyzes_ports(self):
        def model(messages, settings):
            payload = json.loads(messages[-1]['content'])
            if 'constraints' in payload:
                return json.dumps({'args': ['-sT', '-sV', '--version-light', '-p', '22'],
                                   'reason': 'Identify the selected SSH service with light probes.'})
            return self.model(messages, settings)
        with patch('security_tools.shutil.which', return_value='/usr/bin/nmap'), \
                patch('collectors.run', side_effect=self.collect) as run:
            proposal = assistant.analyze_ip('127.0.0.1', authorized=True, tool='nmap', ports='22',
                                            root=self.root, database=self.db, client=model)
            run.assert_not_called()
            result = assistant.analyze_ip('127.0.0.1', authorized=True, tool='nmap', ports='22',
                                          plan_id=proposal['plan_id'], digest=proposal['digest'],
                                          root=self.root, database=self.db, client=model)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], proposal['argv'])
        self.assertEqual(result['port_analyses'][0]['port'], 22)
        self.assertEqual(result['plan_id'], proposal['plan_id'])

    def test_incomplete_command_is_corrected_by_the_model_without_fallback(self):
        seen = []
        def model(messages, settings):
            payload = json.loads(messages[-1]['content']); seen.append(payload)
            args = ['-p', '22'] if len(seen) == 1 else ['-sT', '-sV', '--version-light', '-p', '22']
            return json.dumps({'args': args, 'reason': 'Identify SSH with bounded version probes.'})
        with patch('security_tools.shutil.which', return_value='/usr/bin/nmap'), patch('collectors.run') as run:
            result = assistant.analyze_ip('127.0.0.1', authorized=True, tool='nmap', ports='22',
                                          root=self.root, database=self.db, client=model)
        run.assert_not_called()
        self.assertEqual(len(seen), 2)
        self.assertIn('required evidence capabilities', seen[1]['previous_rejection'])
        self.assertIn('-sV', result['argv'])

    def test_wrong_review_digest_cannot_execute(self):
        with patch('security_tools.shutil.which', return_value='/usr/bin/nmap'):
            plan = assistant.security_tools.proposal_from_response('nmap', '127.0.0.1', 'Review SSH',
                            json.dumps({'args': ['-p', '22'], 'reason': 'Inspect SSH.'}))
            plan = assistant.security_tools.write_report(plan, 'plans', root=self.root)
            with patch('collectors.run') as run, self.assertRaises(ValueError):
                assistant.analyze_ip('127.0.0.1', authorized=True, tool='nmap', ports='22', plan_id=plan['id'],
                                     digest='0' * 64, root=self.root, database=self.db)
            run.assert_not_called()

            with patch('collectors.run') as run, self.assertRaisesRegex(ValueError, 'port scope'):
                assistant.analyze_ip('127.0.0.1', authorized=True, tool='nmap', ports='80', plan_id=plan['id'],
                                     digest=plan['digest'], root=self.root, database=self.db)
            run.assert_not_called()

    def collect(self, argv, output, errors, **limits):
        Path(output).write_bytes(XML); Path(errors).write_text('')

    def test_selected_nmap_uses_policy_and_records_service_evidence(self):
        with patch('security_tools.shutil.which', return_value='/usr/bin/nmap'), \
                patch('collectors.run', side_effect=self.collect) as run, \
                patch('assistant.socket.create_connection') as tcp:
            result = self.analyze_reviewed('127.0.0.1', authorized=True, tool='nmap', ports='22',
                                          root=self.root, database=self.db, client=self.model)
        tcp.assert_not_called()
        argv = run.call_args.args[0]
        self.assertIn('--unprivileged', argv)
        self.assertEqual(argv[-1], '127.0.0.1')
        self.assertEqual(argv[argv.index('-p') + 1], '22')
        self.assertEqual(result['tool'], 'nmap')
        self.assertEqual(result['observations']['open_tcp'][0]['product'], 'FixtureSSH')
        self.assertTrue(Path(result['evidence_files']['nmap_xml']).is_file())

    def test_unknown_tool_and_missing_authorization_never_execute(self):
        with patch('collectors.run') as run:
            for tool, authorized in [('shell', True), ('nmap', False)]:
                with self.assertRaises(ValueError):
                    self.analyze_reviewed('127.0.0.1', tool=tool, authorized=authorized,
                                         root=self.root, database=self.db)
            run.assert_not_called()

    def test_missing_nmap_and_failed_execution_are_logged_without_model_call(self):
        for installed in (False, True):
            with self.subTest(installed=installed), \
                    patch('security_tools.shutil.which', return_value='/usr/bin/nmap' if installed else None), \
                    patch('collectors.run', side_effect=ValueError('execution failed')), \
                    patch('assistant.answer') as model:
                with self.assertRaises(ValueError):
                    self.analyze_reviewed('127.0.0.1', authorized=True, tool='nmap', ports='22',
                                         root=self.root, database=self.db)
                model.assert_not_called()
        records = [json.loads(path.read_text()) for path in (self.root / 'logs/assistant').glob('analysis_*.json')]
        self.assertEqual(len(records), 1)
        self.assertTrue(all(r['status'] == 'failed' for r in records))

    def test_custom_xml_entities_are_not_accepted(self):
        def bad_collect(argv, output, errors, **limits):
            Path(output).write_bytes(XML.replace(b'<!DOCTYPE nmaprun>', b'<!DOCTYPE nmaprun [<!ENTITY x "bad">]>'))
            Path(errors).write_text('')
        with patch('security_tools.shutil.which', return_value='/usr/bin/nmap'), \
                patch('collectors.run', side_effect=bad_collect), self.assertRaisesRegex(ValueError, 'DTD'):
            self.analyze_reviewed('127.0.0.1', authorized=True, tool='nmap', ports='22',
                                 root=self.root, database=self.db, client=self.model)

    def collect_two_ports(self, argv, output, errors, **limits):
        extra = b'<port protocol="tcp" portid="80"><state state="open"/><service name="http" product="FixtureHTTP" version="2.0"/></port>'
        Path(output).write_bytes(XML.replace(b'</ports>', extra + b'</ports>'))
        Path(errors).write_text('')

    def test_each_open_port_has_isolated_evidence_retrieval_and_citations(self):
        seen = []
        def model(messages, settings):
            payload = json.loads(messages[-1]['content'])
            seen.append(payload['observations'])
            port = payload['observations']['open_tcp'][0]['port']
            return json.dumps({'answer': f'Review port {port} [1].', 'sources': [1]})
        def search(query, *args, **kwargs):
            source = 'http.txt' if query.startswith('80 ') else 'ssh.txt'
            return [{'source': source, 'line': 1, 'sha256': 'a' * 64, 'text': source}]
        with patch('security_tools.shutil.which', return_value='/usr/bin/nmap'), \
                patch('collectors.run', side_effect=self.collect_two_ports), \
                patch('rag.search', side_effect=search) as retrieval:
            result = self.analyze_reviewed('127.0.0.1', authorized=True, tool='nmap', ports='22,80,443',
                                          root=self.root, database=self.db, client=model)
        self.assertEqual([item['selected_ports'] for item in seen], [[22], [80]])
        self.assertTrue(all(len(item['open_tcp']) == 1 for item in seen))
        self.assertTrue(retrieval.call_args_list[1].args[0].startswith('22 '))
        self.assertTrue(retrieval.call_args_list[2].args[0].startswith('80 '))
        entries = result['port_analyses']
        self.assertEqual(entries[0]['sources'][0]['source'], 'ssh.txt')
        self.assertEqual(entries[1]['sources'][0]['source'], 'http.txt')
        self.assertEqual(entries[2]['status'], 'not_reported_open')
        self.assertEqual([source['port'] for source in result['sources']], [22, 80])

    def test_one_port_failure_preserves_other_assessments(self):
        def model(messages, settings):
            payload = json.loads(messages[-1]['content'])
            if payload['observations']['open_tcp'][0]['port'] == 22:
                raise ValueError('SSH assessment unavailable')
            return self.model(messages, settings)
        with patch('security_tools.shutil.which', return_value='/usr/bin/nmap'), \
                patch('collectors.run', side_effect=self.collect_two_ports):
            result = self.analyze_reviewed('127.0.0.1', authorized=True, tool='nmap', ports='22,80',
                                          root=self.root, database=self.db, client=model)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['port_analyses'][0]['status'], 'failed')
        self.assertEqual(result['port_analyses'][1]['status'], 'review_required')
        path = self.root / 'logs/assistant' / f"analysis_{result['report_id']}.json"
        persisted = json.loads(path.read_text())
        self.assertEqual(persisted['port_analyses'][1]['answer'], result['port_analyses'][1]['answer'])


if __name__ == '__main__':
    unittest.main()
