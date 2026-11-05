import io
import json
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from import_dataset import ExtractError, extract_text, import_one, safe_name

class ImportTests(unittest.TestCase):
    def test_code_and_security_formats(self):
        sample = b'def check_port(port):\n    if port < 1 or port > 65535:\n        raise ValueError("invalid port")\n    return port\n'
        for name in ('main.py', 'main.kt', 'main.rs', 'script.nse', 'main.ts', 'src.c'):
            with self.subTest(name=name):
                self.assertIn('check_port', extract_text(name, sample))
        self.assertIn('findings', extract_text('scan.sarif',
            json.dumps({'version':'2.1.0','findings':'example result'}).encode()))
        self.assertIn('requests', extract_text('trace.har',
            json.dumps({'log':{'requests':['GET /health']}}).encode()))
        self.assertIn('Nessus', extract_text('scan.nessus',
            b'<NessusClientData_v2><Report>authorized scan result</Report></NessusClientData_v2>'))

    def test_documentation_and_structured_data(self):
        html = b'<html><nav>ignore this</nav><h1>Safe development</h1><p>Always validate untrusted input.</p></html>'
        result=extract_text('guide.html',html)
        self.assertIn('Safe development',result)
        self.assertNotIn('ignore this',result)
        self.assertIn('rule',extract_text('rules.jsonl', b'{"rule":"do not leak secrets"}\n{"rule":"validate inputs"}\n'))
        self.assertIn('validate',extract_text('guide.rst',b'Security guidelines for safe Python development: validate all input.'))

    def test_reject_traversal_and_secret(self):
        for name in ('../secrets.py','/etc/passwd.py','C:\\windows\\file.py','src/../../file.py','secret.py'):
            with self.subTest(name=name):
                with self.assertRaises(ExtractError):
                    safe_name(name)
        with self.assertRaises(ExtractError):
            extract_text('example.py',b'password="supersecret12345"  # secret embedded in sample\n' * 3)

    def test_zip_import_and_zip_slip(self):
        bio=io.BytesIO()
        with zipfile.ZipFile(bio,'w') as z:
            z.writestr('src/main.py', 'def validate_input(value):\n    return value if isinstance(value, int) else None\n')
            z.writestr('../bad.py', 'print("this path attempts directory traversal")\n')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ExtractError):
                import_one('project.zip',bio.getvalue(),'test fixture',tmp)
            self.assertFalse((Path(tmp).parent/'bad.py').exists())

    def test_tar_symlink_rejected(self):
        bio=io.BytesIO()
        with tarfile.open(fileobj=bio,mode='w:gz') as t:
            link=tarfile.TarInfo('src/link.py'); link.type=tarfile.SYMTYPE; link.linkname='/etc/passwd'
            t.addfile(link)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ExtractError):
                import_one('project.tar.gz',bio.getvalue(),'test fixture',tmp)

    def test_valid_archive(self):
        bio=io.BytesIO()
        with zipfile.ZipFile(bio,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('src/main.py','def validate_input(value):\n    return isinstance(value, str) and len(value) < 100\n')
        with tempfile.TemporaryDirectory() as tmp:
            good,bad=import_one('example.zip',bio.getvalue(),'local',tmp)
            self.assertEqual(len(good),1)
            self.assertEqual(bad,[])
            self.assertTrue(Path(good[0]['output']).is_file())

    def test_optional_pdf_and_docx(self):
        try:
            from docx import Document
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest('Optional dependencies not installed')
        d=Document(); d.add_paragraph('Secure coding principles: validate every untrusted value.')
        bio=io.BytesIO(); d.save(bio)
        self.assertIn('Secure coding',extract_text('guide.docx',bio.getvalue()))
        writer=PdfWriter(); writer.add_blank_page(width=200,height=200)
        pdf=io.BytesIO(); writer.write(pdf)
        with self.assertRaises(ExtractError):
            extract_text('empty.pdf',pdf.getvalue())

if __name__ == '__main__':
    unittest.main()
