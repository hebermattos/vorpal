"""Security question formatting and CLI output contracts."""
import sys
import unittest
from unittest.mock import patch

from src import generate


class SecurityQuestionTests(unittest.TestCase):
    def test_question_preserves_portuguese_and_answer_marker(self):
        prompt = generate.security_prompt('  Como proteger minhas senhas?  ')
        self.assertIn('segurança defensiva em português', prompt)
        self.assertTrue(prompt.endswith('Pergunta: Como proteger minhas senhas?\nResposta:'))
        for question in ('', '  ', None):
            with self.assertRaises(ValueError):
                generate.security_prompt(question)

    def test_cli_prints_only_answer_for_question_and_full_text_for_prompt(self):
        for flag in ('--question', '--prompt'):
            with self.subTest(flag=flag), \
                 patch.object(sys, 'argv', ['generate.py', flag, 'Como proteger senhas?']), \
                 patch.object(generate, 'configure_cpu'), \
                 patch.object(generate.keras.models, 'load_model'), \
                 patch.object(generate, 'generate', side_effect=lambda model, prompt, *args: prompt + 'Use MFA.'), \
                 patch('builtins.print') as output:
                generate.main()
                expected = 'Use MFA.' if flag == '--question' else 'Como proteger senhas?Use MFA.'
                output.assert_called_once_with(expected)
