"""The external service client sends inference requests, not lifecycle commands."""
import json
from pathlib import Path
import sys
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import scan_intelligence as inference


class InferenceClientTests(unittest.TestCase):
    def test_configured_endpoint_and_model_are_used_for_inference_only(self):
        settings = dict(inference.SETTINGS, host='localhost', port=11435, model='selected-model:latest')
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = json.dumps({
            'choices': [{'message': {'content': '{"answer":"Advice","sources":[1]}'}}]
        }).encode()
        with patch.object(inference.urllib.request, 'build_opener', return_value=opener):
            result = inference._local_chat([{'role': 'user', 'content': 'SSH security'}], settings)
        self.assertIn('Advice', result)
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'http://localhost:11435/v1/chat/completions')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(json.loads(request.data)['model'], 'selected-model:latest')

    def test_unavailable_service_reports_configuration_without_retrying_setup(self):
        opener = MagicMock()
        opener.open.side_effect = urllib.error.URLError('Connection refused')
        with patch.object(inference.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(inference.AnalysisError, 'External Ollama service unavailable') as error:
                inference._local_chat([], inference.SETTINGS)
        opener.open.assert_called_once()
        self.assertIn('config/scan_intelligence.json', str(error.exception))
        self.assertIn(inference.SETTINGS['model'], str(error.exception))


if __name__ == '__main__':
    unittest.main()
