"""Safe, bounded import of licensed documents, code and security data into Vorpal corpus.

No code execution, archive extraction to disk, crawling or OCR. Review all content and licenses.
"""
import argparse
import hashlib
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 16 * 1024 * 1024
MAX_MEMBERS = 100
MAX_RATIO = 100
MAX_PAGES = 100
MAX_REDIRECTS = 3
MAX_URLS_PER_FOLDER = 100
MAX_URLS_TOTAL = 250
MAX_URL_LIST_BYTES = 64 * 1024
MAX_URL_LENGTH = 4096

CODE = {'.py', '.js', '.jsx', '.ts', '.tsx', '.java', '.kt', '.kts',
        '.go', '.rs', '.c', '.h', '.cpp', '.hpp', '.cs', '.sh', '.nse'}
DOCS = {'.txt', '.md', '.rst', '.html', '.htm', '.pdf', '.docx'}
DATA = {'.json', '.jsonl', '.csv', '.xml', '.toml', '.yaml', '.yml'}
SECURITY = {'.sarif', '.nessus', '.har'}
SUPPORTED = CODE | DOCS | DATA | SECURITY
SKIP_DIRS = {'.git', '.venv', 'node_modules', '__pycache__', 'dist', 'build'}
SENSITIVE = re.compile(r'(^\.env|secret|credential|private[_-]?key|id_rsa|\.pem$|\.key$|password|token)', re.I)
SECRET = re.compile(
    r'-----BEGIN [A-Z ]*PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9_]{20,}|'
    r'AKIA[0-9A-Z]{16}|(?i:api[_-]?key|password|secret|token)\s*[:=]\s*["\x27]?[^\s"\x27]{8,}'
)

class ExtractError(ValueError):
    pass

class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'nav', 'footer', 'header', 'noscript', 'svg', 'form'}:
            self.skip += 1
        elif not self.skip and tag in {'p', 'div', 'h1', 'h2', 'h3', 'li', 'pre', 'br', 'tr'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'nav', 'footer', 'header', 'noscript', 'svg', 'form'}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, text):
        if not self.skip:
            self.parts.append(text)

def safe_name(name):
    # Archive member paths are always POSIX-style, even on Windows.
    if '\\' in name or name.startswith('/') or re.match(r'^[A-Za-z]:', name):
        raise ExtractError('caminho absoluto ou Windows em arquivo compactado')
    parts = PurePosixPath(name).parts
    if not parts or '..' in parts or '.' in parts or any(x in SKIP_DIRS for x in parts):
        raise ExtractError('caminho inválido em arquivo compactado')
    if any(SENSITIVE.search(p) for p in parts):
        raise ExtractError('nome potencialmente sensível')
    return PurePosixPath(name)

def classify(name):
    low = name.lower()
    if low.endswith('.tar.gz') or low.endswith('.tgz'):
        return 'tar'
    if low.endswith('.zip'):
        return 'zip'
    return Path(low).suffix

def extract_text(name, raw):
    kind = classify(name)
    if kind not in SUPPORTED:
        raise ExtractError('formato não suportado')
    if len(raw) > MAX_FILE:
        raise ExtractError('arquivo maior que 8 MiB')
    if kind == '.pdf':
        try:
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(raw), strict=True)
            if reader.is_encrypted or len(reader.pages) > MAX_PAGES:
                raise ExtractError('PDF criptografado ou com páginas demais')
            content = '\n'.join(page.extract_text() or '' for page in reader.pages)
        except ImportError as exc:
            raise ExtractError('instale pypdf para importar PDFs') from exc
        except ExtractError:
            raise
        except Exception as exc:
            raise ExtractError('PDF inválido ou impossível de extrair') from exc
    elif kind == '.docx':
        # DOCX is a ZIP; reject oversized members before handing it to python-docx.
        try:
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                entries = archive.infolist()
                if len(entries) > MAX_MEMBERS or sum(x.file_size for x in entries) > MAX_TOTAL:
                    raise ExtractError('DOCX excessivamente grande')
                if any(x.file_size > MAX_FILE or
                       (x.compress_size and x.file_size / x.compress_size > MAX_RATIO)
                       for x in entries):
                    raise ExtractError('DOCX com expansão suspeita')
            from docx import Document
            doc = Document(BytesIO(raw))
            content = '\n'.join([p.text for p in doc.paragraphs] +
                                [cell.text for table in doc.tables for row in table.rows for cell in row.cells])
        except ImportError as exc:
            raise ExtractError('instale python-docx para importar DOCX') from exc
        except ExtractError:
            raise
        except Exception as exc:
            raise ExtractError('DOCX inválido ou impossível de extrair') from exc
    else:
        if b'\x00' in raw:
            raise ExtractError('conteúdo binário inesperado')
        try:
            content = raw.decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            raise ExtractError('arquivo não codificado em UTF-8') from exc
        if kind in {'.html', '.htm'}:
            p = TextHTML()
            p.feed(content)
            content = ''.join(p.parts)
        elif kind in {'.json', '.jsonl', '.sarif', '.har'}:
            try:
                if kind == '.jsonl':
                    objects = [json.loads(line) for line in content.splitlines() if line.strip()]
                    content = '\n'.join(json.dumps(o, ensure_ascii=False) for o in objects)
                else:
                    content = json.dumps(json.loads(content), indent=2, ensure_ascii=False)
            except (ValueError, TypeError) as exc:
                raise ExtractError('JSON inválido') from exc
    content = re.sub(r'\n{3,}', '\n\n', content).strip()
    if len(content) < 50:
        raise ExtractError('texto extraído insuficiente')
    if SECRET.search(content):
        raise ExtractError('possível credencial/segredo encontrado: revise fora do importador')
    return content

def iter_archive(name, raw):
    typ = classify(name)
    if typ == 'zip':
        try:
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                entries = archive.infolist()
                if len(entries) > MAX_MEMBERS:
                    raise ExtractError('arquivo compactado com entradas demais')
                total = 0
                for info in entries:
                    if info.is_dir():
                        continue
                    path = safe_name(info.filename)
                    mode = (info.external_attr >> 16) & 0xFFFF
                    if stat.S_ISLNK(mode):
                        raise ExtractError('links simbólicos não são permitidos')
                    if classify(str(path)) not in SUPPORTED:
                        continue
                    if info.flag_bits & 0x1:
                        raise ExtractError('ZIP criptografado não é permitido')
                    total += info.file_size
                    if total > MAX_TOTAL or info.file_size > MAX_FILE:
                        raise ExtractError('limite descompactado excedido')
                    if info.file_size and (not info.compress_size or info.file_size / info.compress_size > MAX_RATIO):
                        raise ExtractError('taxa de compactação suspeita')
                    with archive.open(info) as stream:
                        blob = stream.read(MAX_FILE + 1)
                    if len(blob) > MAX_FILE:
                        raise ExtractError('limite de entrada excedido')
                    yield str(path), blob
        except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
            raise ExtractError('ZIP inválido') from exc
    elif typ == 'tar':
        try:
            with tarfile.open(fileobj=BytesIO(raw), mode='r:gz') as archive:
                members = archive.getmembers()
                if len(members) > MAX_MEMBERS:
                    raise ExtractError('TAR com entradas demais')
                total = 0
                for member in members:
                    if member.isdir():
                        continue
                    if not member.isfile():
                        raise ExtractError('links ou entradas especiais não são permitidos')
                    path = safe_name(member.name)
                    if classify(str(path)) not in SUPPORTED:
                        continue
                    total += member.size
                    if member.size > MAX_FILE or total > MAX_TOTAL:
                        raise ExtractError('limite descompactado excedido')
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ExtractError('entrada TAR ilegível')
                    with stream:
                        blob = stream.read(MAX_FILE + 1)
                    if len(blob) > MAX_FILE:
                        raise ExtractError('limite de entrada excedido')
                    yield str(path), blob
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise ExtractError('TAR inválido') from exc

def fetch_url(url):
    # DNS preflight inherited from existing importer; not a complete protection
    # against DNS rebinding. Run this tool in a restricted-network environment.
    from ingest_url import validate_url, NoRedirect
    opener = urllib.request.build_opener(NoRedirect())
    for _ in range(MAX_REDIRECTS + 1):
        validate_url(url)
        req = urllib.request.Request(url, headers={'User-Agent': 'VorpalDatasetImporter/1.0'})
        try:
            response = opener.open(req, timeout=20)
        except urllib.error.HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                raise
            location = exc.headers.get('Location')
            if not location:
                raise ExtractError('redirecionamento sem Location')
            url = urllib.parse.urljoin(url, location)
            continue
        with response:
            size = response.headers.get('Content-Length')
            if size and int(size) > MAX_FILE:
                raise ExtractError('download maior que 8 MiB')
            data = response.read(MAX_FILE + 1)
            if len(data) > MAX_FILE:
                raise ExtractError('download maior que 8 MiB')
            path = urllib.parse.urlsplit(url).path
            name = urllib.parse.unquote(PurePosixPath(path).name)
            if classify(name) not in SUPPORTED | {'zip', 'tar'}:
                media = response.headers.get_content_type()
                name = {'text/html':'page.html','text/plain':'page.txt',
                        'application/json':'page.json','application/pdf':'page.pdf',
                        'application/zip':'archive.zip',
                        'application/vnd.openxmlformats-officedocument.wordprocessingml.document':'page.docx'}.get(media, '')
            if not name:
                raise ExtractError('tipo de URL não suportado')
            return name, data, url
    raise ExtractError('redirecionamentos demais')

def import_one(name, raw, provenance, out):
    out = Path(out)
    accepted, rejected = [], []
    entries = iter_archive(name, raw) if classify(name) in {'zip', 'tar'} else [(name, raw)]
    for entry_name, blob in entries:
        try:
            safe_name(entry_name)
            content = extract_text(entry_name, blob)
            digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
            target = out / (digest[:24] + '.txt')
            out.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_text(content + '\n', encoding='utf-8')
            accepted.append({'source': provenance, 'member': entry_name, 'output': str(target), 'sha256': digest})
        except ExtractError as exc:
            rejected.append({'member': entry_name, 'reason': str(exc)})
    return accepted, rejected

def collect_directory(root):
    """Collect supported local files plus URLs from any file named exactly 'url'.

    A URL list uses UTF-8, one URL per line; empty lines and # comments are ignored.
    Failures are recorded per line without interrupting other files or URLs.
    """
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise ExtractError('diretório não encontrado ou link simbólico')
    sources, rejected, seen_urls = [], [], set()
    total_urls = 0
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if any(part in SKIP_DIRS for part in relative.parts) or path.is_symlink():
            continue
        if not path.is_file():
            continue
        if path.name == 'url':
            if path.stat().st_size > MAX_URL_LIST_BYTES:
                rejected.append({'source': str(path), 'reason': 'lista URL maior que 64 KiB'})
                continue
            try:
                lines = path.read_text(encoding='utf-8-sig').splitlines()
            except (UnicodeError, OSError) as exc:
                rejected.append({'source': str(path), 'reason': f'lista URL ilegível: {exc}'})
                continue
            count = 0
            for line_no, raw_line in enumerate(lines, start=1):
                url = raw_line.strip()
                if not url or url.startswith('#'):
                    continue
                count += 1
                source = f'{path}:{line_no}'
                if count > MAX_URLS_PER_FOLDER or total_urls >= MAX_URLS_TOTAL:
                    rejected.append({'source': source, 'reason': 'limite de URLs excedido'})
                    continue
                total_urls += 1
                if len(url) > MAX_URL_LENGTH:
                    rejected.append({'source': source, 'reason': 'URL longa demais'})
                    continue
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                try:
                    name, raw, final_url = fetch_url(url)
                    sources.append((name, raw, final_url))
                except (ExtractError, ValueError, urllib.error.URLError, OSError) as exc:
                    rejected.append({'source': source, 'url': url, 'reason': str(exc)})
            continue
        if classify(path.name) not in SUPPORTED | {'zip', 'tar'}:
            continue
        try:
            if path.stat().st_size > MAX_FILE:
                rejected.append({'source': str(path), 'reason': 'arquivo maior que 8 MiB'})
                continue
            sources.append((path.name, path.read_bytes(), str(path)))
        except OSError as exc:
            rejected.append({'source': str(path), 'reason': str(exc)})
    return sources, rejected

def main():
    p = argparse.ArgumentParser(description='Importar arquivos locais, diretórios, URL, ZIP e TAR.GZ para o corpus do Vorpal')
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--file')
    group.add_argument('--directory')
    group.add_argument('--url')
    p.add_argument('--out', default='corpus/imported')
    args = p.parse_args()
    output = Path(args.out)
    sources = []
    rejected = []
    try:
        if args.url:
            name, raw, url = fetch_url(args.url)
            sources = [(name, raw, url)]
        elif args.file:
            path = Path(args.file)
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE:
                raise ExtractError('arquivo inválido, link simbólico ou maior que 8 MiB')
            sources = [(path.name, path.read_bytes(), str(path))]
        else:
            sources, rejected = collect_directory(args.directory)
        accepted = []
        for name, data, provenance in sources:
            if SENSITIVE.search(name):
                rejected.append({'source': provenance, 'reason': 'nome potencialmente sensível'})
                continue
            try:
                a, r = import_one(name, data, provenance, output)
                accepted.extend(a)
                rejected.extend([{'source': provenance, **item} for item in r])
            except (ExtractError, OSError) as exc:
                rejected.append({'source': provenance, 'reason': str(exc)})
        if accepted or rejected:
            output.mkdir(parents=True, exist_ok=True)
            report = output / 'import_report.json'
            report.write_text(json.dumps({'accepted': accepted, 'rejected': rejected},
                                         indent=2, ensure_ascii=False), encoding='utf-8')
        print(f'Importados: {len(accepted)}; rejeitados: {len(rejected)}')
        if rejected:
            for item in rejected[:20]:
                print('REJEITADO:', item)
        if not accepted:
            p.exit(1, 'Nenhum arquivo importado.\n')
        print('Revise as licenças e os arquivos em corpus/imported antes de preparar data/.')
        print('python src/data.py --corpus corpus --out data')
    except (ExtractError, OSError, ValueError, urllib.error.URLError) as exc:
        p.exit(1, f'Erro: {exc}\n')

if __name__ == '__main__':
    main()
