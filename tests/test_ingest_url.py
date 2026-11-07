import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from import_dataset import TextHTML as HTMLText, validate_url
from ingest_url import main

class IngestUrlTests(unittest.TestCase):
    def test_extraction_removes_scripts_and_navigation(self):
        parser=HTMLText()
        parser.feed('<html><nav>Menu</nav><main><h1>Secure Python</h1><p>Use parameterized SQL.</p><script>BAD()</script></main></html>')
        output=parser.text()
        self.assertIn('Secure Python',output)
        self.assertIn('Use parameterized SQL.',output)
        self.assertNotIn('Menu',output)
        self.assertNotIn('BAD()',output)

    def test_reject_credentials_and_unsupported_scheme(self):
        for url in ('file:///etc/passwd','https://u:p@example.com/a','ftp://example.com'):
            with self.subTest(url=url):
                with self.assertRaises(ValueError): validate_url(url)

    def test_reject_private_and_local_hosts(self):
        with patch('import_dataset.socket.getaddrinfo', return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError): validate_url('https://example.com/')
        with self.assertRaises(ValueError): validate_url('http://localhost/')

    def test_public_host(self):
        with patch('import_dataset.socket.getaddrinfo', return_value=[(2,1,6,'',('93.184.215.14',443))]):
            self.assertEqual(validate_url('https://example.com/path'), 'https://example.com/path')

    def test_legacy_cli_forwards_to_canonical_importer(self):
        with patch('ingest_url.importer_main') as importer:
            main(['https://example.com/guide.pdf', '--out', 'custom-corpus'])
        importer.assert_called_once_with(['--url', 'https://example.com/guide.pdf', '--out', 'custom-corpus'])
if __name__=='__main__': unittest.main()
