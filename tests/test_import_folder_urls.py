import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from import_dataset import collect_directory, import_directory, main

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
                '# documentation links\n\nhttps://example.org/a.md\n'
                'https://example.org/a.md\nhttps://example.org/b.py\n',
                encoding='utf-8')
            output = io.StringIO()
            with patch('import_dataset.fetch_url', side_effect=lambda u:
                       (u.rsplit('/',1)[-1], TEXT, u)) as fetch, redirect_stdout(output):
                sources, errors = collect_directory(root)
            self.assertEqual(errors, [])
            self.assertEqual(len(sources), 4)
            self.assertEqual(fetch.call_count, 2)
            self.assertNotIn('url', [x[0] for x in sources])
            self.assertIn('Importing file:', output.getvalue())
            self.assertIn('Importing URL: https://example.org/a.md', output.getvalue())

    def test_url_failure_does_not_block_local_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'guide.txt').write_bytes(TEXT)
            (root/'url').write_text('invalid-link\nhttps://example.org/ok.txt\n')
            def fake_fetch(url):
                if url == 'invalid-link':
                    raise ValueError('invalid URL')
                return 'ok.txt', TEXT, url
            with patch('import_dataset.fetch_url', side_effect=fake_fetch):
                sources, rejected=collect_directory(root)
            self.assertEqual(len(sources), 2)
            self.assertEqual(len(rejected), 1)
            self.assertIn(':1',rejected[0]['source'])

    def test_manifest_accepts_long_links_and_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'url').write_text('# comment\n\n' + 'x'*4097 + '\n')
            with patch('import_dataset.fetch_url', return_value=('guide.txt', TEXT, 'long-url')) as fetch:
                sources, rejected=collect_directory(root)
            self.assertEqual(len(sources),1)
            self.assertEqual(rejected,[])
            fetch.assert_called_once()

    def test_manifest_has_no_250_url_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            urls = [f'https://example.org/doc-{index}.txt' for index in range(251)]
            (root / 'url').write_text('\n'.join(urls), encoding='utf-8')
            with patch('import_dataset.fetch_url', side_effect=lambda url: ('guide.txt', TEXT, url)) as fetch:
                sources, rejected = collect_directory(root)
            self.assertEqual(len(sources), 251)
            self.assertEqual(rejected, [])
            self.assertEqual(fetch.call_count, 251)

    def test_import_directory_skips_sources_already_imported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'corpus'
            output = root / 'imported'
            root.mkdir()
            source_file = root / 'guide.md'
            source_file.write_bytes(TEXT)
            (root / 'url').write_text('https://example.org/guide.txt\n', encoding='utf-8')
            logs = io.StringIO()
            with patch('import_dataset.fetch_url', return_value=('guide.txt', TEXT, 'https://example.org/guide.txt')) as fetch, \
                 redirect_stdout(logs):
                first_import, first_errors = import_directory(root, output)
                second_import, second_errors = import_directory(root, output)
            self.assertEqual(len(first_import), 2)
            self.assertEqual(first_errors, [])
            self.assertEqual(second_import, [])
            self.assertEqual(second_errors, [])
            fetch.assert_called_once()
            self.assertIn('Skipping previously imported URL:', logs.getvalue())
            self.assertIn('Skipping previously imported file:', logs.getvalue())

            with patch('import_dataset.fetch_url', return_value=('guide.txt', TEXT, 'https://example.org/guide.txt')) as fetch, \
                 redirect_stdout(io.StringIO()):
                reset_import, reset_errors = import_directory(root, output, force=True)
            self.assertEqual(len(reset_import), 2)
            self.assertEqual(reset_errors, [])
            fetch.assert_called_once()

            source_file.write_bytes(TEXT + b'Updated guidance after the first import.\n')
            with patch('import_dataset.fetch_url') as fetch, redirect_stdout(io.StringIO()):
                changed_import, changed_errors = import_directory(root, output)
            self.assertEqual(len(changed_import), 1)
            self.assertEqual(changed_errors, [])
            fetch.assert_not_called()

    def test_main_report_captures_download_errors_and_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'sources'; root.mkdir()
            out=Path(tmp)/'output'
            (root/'sample.md').write_bytes(TEXT)
            (root/'url').write_text('https://example.org/bad\nhttps://example.org/ok.txt\n')
            def fake_fetch(url):
                if url.endswith('/bad'):
                    raise ValueError('rejected URL')
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
