"""Offline contracts for shared CLI/UI Ollama + RAG behavior."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import assistant
import rag


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        corpus = self.root / 'corpus'
        corpus.mkdir()
        (corpus / 'security.txt').write_text('Network security hardening firewall SSH access: use public key authentication and restrict access to authorized users.\n')
        self.db = self.root / 'index.sqlite3'
        rag.index(corpus, self.db)
        self.responses = []

    def model(self, messages, settings):
        self.responses.append(json.loads(messages[-1]['content']))
        return json.dumps({'answer': 'Restrict SSH access and use public key authentication [1].', 'sources': [1]})

    def test_conversation_retains_history_and_citations(self):
        first = assistant.chat('How can I secure SSH?', root=self.root, database=self.db, client=self.model)
        second = assistant.chat('What about keys?', session_id=first['session_id'], root=self.root, client=self.model)
        self.assertEqual(first['session_id'], second['session_id'])
        self.assertEqual(self.responses[-1]['conversation'][0]['user'], 'How can I secure SSH?')
        self.assertEqual(second['sources'][0]['source'], 'security.txt')
        self.assertFalse(second['automatic_execution'])
        self.assertEqual(len(list((self.root / 'logs/assistant').glob('chat_*.json'))), 1)

    def test_missing_rag_or_no_results_never_calls_model(self):
        model = MagicMock()
        with self.assertRaisesRegex(ValueError, 'RAG index is required'):
            assistant.answer('SSH security', database=self.root / 'missing', client=model)
        with self.assertRaisesRegex(ValueError, 'no relevant documents'):
            assistant.answer('unmatchableword', database=self.db, client=model)
        model.assert_not_called()

    def test_invalid_model_output_and_citations_are_rejected(self):
        for result in ('not JSON', '[]', json.dumps({'answer': 'Advice', 'sources': [True]}),
                       json.dumps({'answer': 'Advice', 'sources': [999]}),
                       json.dumps({'answer': 'Advice [999]', 'sources': [1]})):
            with self.subTest(result=result), self.assertRaises(ValueError):
                assistant.answer('SSH security', database=self.db, client=lambda *args: result)

    def test_authorization_and_scope_checked_before_connection(self):
        with patch.object(assistant.socket, 'create_connection') as connect:
            for target, authorized in [('192.168.1.10', False), ('8.8.8.8', True), ('localhost', True)]:
                with self.assertRaises(ValueError):
                    assistant.analyze_ip(target, authorized=authorized, database=self.db, root=self.root)
            connect.assert_not_called()

    def test_analysis_logs_evidence_and_chat_can_discuss_report(self):
        connection = MagicMock()
        connection.__enter__.return_value.recv.return_value = b'SSH fixture'
        with patch.object(assistant.socket, 'create_connection', return_value=connection) as connect:
            result = assistant.analyze_ip('127.0.0.1', authorized=True, ports='22', database=self.db,
                                          root=self.root, client=self.model)
        self.assertEqual(connect.call_args.args[0], ('127.0.0.1', 22))
        self.assertEqual(result['status'], 'review_required')
        self.assertEqual(result['observations']['open_tcp'][0]['banner'], 'SSH fixture')
        assistant.chat('How do I secure this SSH service?', report_id=result['report_id'],
                       database=self.db, root=self.root, client=self.model)
        self.assertEqual(self.responses[-1]['observations']['target'], '127.0.0.1')

    def test_analysis_failure_preserves_collected_evidence(self):
        with patch.object(assistant.socket, 'create_connection', side_effect=TimeoutError), \
                self.assertRaisesRegex(ValueError, 'offline'):
            assistant.analyze_ip('127.0.0.1', authorized=True, ports='22', database=self.db,
                                 root=self.root, client=MagicMock(side_effect=ValueError('offline')))
        record = json.loads(next((self.root / 'logs/assistant').glob('analysis_*.json')).read_text())
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['observations']['unreachable_or_closed'], [22])

    def test_invalid_ports_and_session_paths_are_rejected(self):
        for value in ('0', '22;touch bad', '65536', [True], []):
            with self.assertRaises(ValueError):
                assistant.ports_from_text(value)
        with self.assertRaises(ValueError):
            assistant.chat('SSH', session_id='../../outside', root=self.root, client=self.model)
        with self.assertRaisesRegex(ValueError, 'Conversation not found'):
            assistant.chat('SSH', session_id='a' * 24, root=self.root, client=self.model)


if __name__ == '__main__':
    unittest.main()
