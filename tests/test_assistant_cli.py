"""Default command routing uses the shared Ollama + RAG assistant."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import vorpal
import assistant
import contextlib
import io


class AssistantCLITests(unittest.TestCase):
    def test_selected_tool_is_forwarded_to_analysis(self):
        with patch('assistant.analyze_ip', return_value={'tool': 'nmap'}) as analyze, contextlib.redirect_stdout(io.StringIO()):
            assistant.main(['analyze', '--target', '127.0.0.1', '--authorized', '--tool', 'nmap', '--ports', '22'])
        self.assertEqual(analyze.call_args.kwargs['tool'], 'nmap')
        self.assertTrue(analyze.call_args.kwargs['authorized'])

    def test_explicit_chat_and_analysis_use_shared_assistant(self):
        for arguments in (['chat', '--message', 'SSH security'],
                          ['analyze', '--target', '127.0.0.1', '--authorized']):
            with patch.object(sys, 'argv', ['vorpal.py', *arguments]), \
                    patch('assistant.main', return_value=0) as main:
                self.assertEqual(vorpal.main(), 0)
                main.assert_called_once_with(arguments)

    def test_positional_ip_uses_ollama_analysis(self):
        with patch.object(sys, 'argv', ['vorpal.py', '127.0.0.1', '--authorized', '--ports', '22']), \
                patch('assistant.main', return_value=0) as main:
            vorpal.main()
            main.assert_called_once_with(['analyze', '--target', '127.0.0.1', '--authorized', '--ports', '22'])


if __name__ == '__main__':
    unittest.main()
