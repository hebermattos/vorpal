import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from import_dataset import collect_directory, main

TEXT = b'Secure development requires validating untrusted inputs, checking limits and testing behavior.\n'

class FolderURLTests(unittest.TestCase):
    def test_folder_and_nested_url_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'local.py').write_bytes(TEXT)
            nested = root / 'documentation'
            nested.mkdir()
            (nested / 'guide.md').write_bytes(TEXT + b'An additional paragraph about test coverage.\n')
            (nested / 'url').write_text(
                '# links de documentacao\n\nhttps://example.org/a.md\n'
                'https://example.org/a.md\nhttps://example.org/b.py\n',
                encoding='utf-8')
            with patch('import_dataset.fetch_url', side_effect=lambda u:
                       (u.rsplit('/',1)[-1], TEXT, u)) as fetch:
                sources, errors = collect_directory(root)
            self.assertEqual(errors, [])
            self.assertEqual(len(sources), 4)
            self.assertEqual(fetch.call_count, 2)
            self.assertNotIn('url', [x[0] for x in sources])

    def test_url_failure_does_not_block_local_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'guide.txt').write_bytes(TEXT)
            (root/'url').write_text('invalid-link\nhttps://example.org/ok.txt\n')
            def fake_fetch(url):
                if url == 'invalid-link':
                    raise ValueError('URL inválida')
                return 'ok.txt', TEXT, url
            with patch('import_dataset.fetch_url', side_effect=fake_fetch):
                sources, rejected=collect_directory(root)
            self.assertEqual(len(sources), 2)
            self.assertEqual(len(rejected), 1)
            self.assertIn(':1',rejected[0]['source'])

    def test_manifest_with_long_link_and_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'url').write_text('# comentário\n\n' + 'x'*4097 + '\n')
            with patch('import_dataset.fetch_url') as fetch:
                sources, rejected=collect_directory(root)
            self.assertEqual(sources,[])
            self.assertEqual(len(rejected),1)
            self.assertIn('longa',rejected[0]['reason'])
            fetch.assert_not_called()

    def test_main_report_captures_download_errors_and_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'sources'; root.mkdir()
            out=Path(tmp)/'output'
            (root/'sample.md').write_bytes(TEXT)
            (root/'url').write_text('https://example.org/bad\nhttps://example.org/ok.txt\n')
            def fake_fetch(url):
                if url.endswith('/bad'):
                    raise ValueError('URL rejeitada')
                return 'ok.txt', TEXT+b'More unique safety guidance.\n',url
            output=io.StringIO()
            with patch('import_dataset.fetch_url', side_effect=fake_fetch), \
                 patch.object(sys, 'argv', ['import_dataset.py','--directory',str(root),'--out',str(out)]), \
                 redirect_stdout(output):
                main()
            report=json.loads((out/'import_report.json').read_text())
            self.assertEqual(len(report['accepted']),2)
            self.assertEqual(len(report['rejected']),1)
            self.assertTrue((out/'import_report.json').is_file())
            self.assertTrue(all(Path(p['output']).is_file() for p in report['accepted']))

    def test_symlink_manifest_not_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'sources';root.mkdir()
            outside=Path(tmp)/'outside';outside.write_text('https://example.org/danger.txt\n')
            (root/'url').symlink_to(outside)
            with patch('import_dataset.fetch_url') as fetch:
                sources, rejected=collect_directory(root)
            self.assertEqual(sources,[])
            fetch.assert_not_called()

if __name__=='__main__':
    unittest.main()
