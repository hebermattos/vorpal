"""Safe import of licensed documents, code and security data into Vorpal corpus.

No code execution, archive extraction to disk or OCR. Review all content and licenses.
"""

import argparse
import hashlib
import ipaddress
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
import stat
import socket
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from content_safety import SECRET_CONTENT
from data import is_internal_file

try:
    from .config import load_config
except ImportError:  # Script invocation.
    from config import load_config

SETTINGS = load_config("import_dataset")

MAX_MEMBERS = SETTINGS["policy"]["max_members"]
MAX_RATIO = SETTINGS["policy"]["max_ratio"]
MAX_REDIRECTS = SETTINGS["policy"]["max_redirects"]

CODE = set(SETTINGS["policy"]["code"])
DOCS = set(SETTINGS["policy"]["docs"])
DATA = set(SETTINGS["policy"]["data"])
SECURITY = set(SETTINGS["policy"]["security"])
SUPPORTED = CODE | DOCS | DATA | SECURITY
SKIP_DIRS = set(SETTINGS["policy"]["skip_dirs"])
SENSITIVE = re.compile(SETTINGS["policy"]["sensitive"], re.I)
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

    def text(self):
        return re.sub(r'\n{3,}', '\n\n', ''.join(self.parts)).strip()

def safe_name(name):
    # Archive member paths are always POSIX-style, even on Windows.
    if '\\' in name or name.startswith('/') or re.match(r'^[A-Za-z]:', name):
        raise ExtractError('absolute or Windows path in archive member')
    parts = PurePosixPath(name).parts
    if not parts or '..' in parts or '.' in parts or any(x in SKIP_DIRS for x in parts):
        raise ExtractError('invalid path in archive member')
    if any(SENSITIVE.search(p) for p in parts):
        raise ExtractError('potentially sensitive name')
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
        raise ExtractError('unsupported format')
    if kind == '.pdf':
        try:
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(raw), strict=True)
            if reader.is_encrypted:
                raise ExtractError('encrypted PDF')
            content = '\n'.join(page.extract_text() or '' for page in reader.pages)
        except ImportError as exc:
            raise ExtractError('install pypdf to import PDFs') from exc
        except ExtractError:
            raise
        except Exception as exc:
            raise ExtractError('invalid PDF or unable to extract text') from exc
    elif kind == '.docx':
        # DOCX is a ZIP; reject oversized members before handing it to python-docx.
        try:
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                entries = archive.infolist()
                if len(entries) > MAX_MEMBERS:
                    raise ExtractError('DOCX has too many members')
                if any(x.compress_size and x.file_size / x.compress_size > MAX_RATIO
                       for x in entries):
                    raise ExtractError('suspicious DOCX expansion ratio')
            from docx import Document
            doc = Document(BytesIO(raw))
            content = '\n'.join([p.text for p in doc.paragraphs] +
                                [cell.text for table in doc.tables for row in table.rows for cell in row.cells])
        except ImportError as exc:
            raise ExtractError('install python-docx to import DOCX') from exc
        except ExtractError:
            raise
        except Exception as exc:
            raise ExtractError('invalid DOCX or unable to extract text') from exc
    else:
        if b'\x00' in raw:
            raise ExtractError('unexpected binary content')
        try:
            content = raw.decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            raise ExtractError('file is not UTF-8 encoded') from exc
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
                raise ExtractError('invalid JSON') from exc
    content = re.sub(r'\n{3,}', '\n\n', content).strip()
    if len(content) < SETTINGS["runtime"]["min_text_length"]:
        raise ExtractError('insufficient extracted text')
    if SECRET_CONTENT.search(content):
        raise ExtractError('possible credential/secret found: review outside the importer')
    return content

def iter_archive(name, raw):
    typ = classify(name)
    if typ == 'zip':
        try:
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                entries = archive.infolist()
                if len(entries) > MAX_MEMBERS:
                    raise ExtractError('archive has too many entries')
                for info in entries:
                    if info.is_dir():
                        continue
                    path = safe_name(info.filename)
                    mode = (info.external_attr >> 16) & 0xFFFF
                    if stat.S_ISLNK(mode):
                        raise ExtractError('symbolic links are not allowed')
                    if classify(str(path)) not in SUPPORTED:
                        continue
                    if info.flag_bits & 0x1:
                        raise ExtractError('encrypted ZIP is not allowed')
                    if info.file_size and (not info.compress_size or info.file_size / info.compress_size > MAX_RATIO):
                        raise ExtractError('suspicious compression ratio')
                    with archive.open(info) as stream:
                        blob = stream.read()
                    yield str(path), blob
        except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
            raise ExtractError('invalid ZIP') from exc
    elif typ == 'tar':
        try:
            with tarfile.open(fileobj=BytesIO(raw), mode='r:gz') as archive:
                members = archive.getmembers()
                if len(members) > MAX_MEMBERS:
                    raise ExtractError('TAR archive has too many entries')
                for member in members:
                    if member.isdir():
                        continue
                    if not member.isfile():
                        raise ExtractError('special links or entries are not allowed')
                    path = safe_name(member.name)
                    if classify(str(path)) not in SUPPORTED:
                        continue
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ExtractError('unreadable TAR member')
                    with stream:
                        blob = stream.read()
                    yield str(path), blob
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise ExtractError('invalid TAR') from exc


def validate_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Provide a public HTTP(S) URL without credentials.')
    if parsed.port not in (None, 80, 443):
        raise ValueError('Ports other than 80 and 443 are not allowed.')
    host = parsed.hostname.rstrip('.')
    if host.lower() == 'localhost' or host.lower().endswith('.localhost'):
        raise ValueError('Local addresses are not allowed.')
    try:
        addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == 'https' else 80),
                                       type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError('Could not resolve the host.') from exc
    if not addresses or not all(ipaddress.ip_address(info[4][0]).is_global for info in addresses):
        raise ValueError('Private, reserved, or local address is blocked.')
    return url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def fetch_url(url, *, allowed_host=None, max_bytes=None, allow_url=None):
    # DNS preflight inherited from existing importer; not a complete protection
    # against DNS rebinding. Run this tool in a restricted-network environment.
    opener = urllib.request.build_opener(NoRedirect())
    for _ in range(MAX_REDIRECTS + 1):
        if allowed_host is not None and urllib.parse.urlsplit(url).hostname != allowed_host:
            raise ExtractError('scraping redirect outside the seed hostname is blocked')
        validate_url(url)
        if allow_url is not None:
            allow_url(url)
        req = urllib.request.Request(url, headers={'User-Agent': SETTINGS['runtime']['user_agent']})
        try:
            response = opener.open(req, timeout=SETTINGS["runtime"]["timeout"])
        except urllib.error.HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                raise
            location = exc.headers.get('Location')
            if not location:
                raise ExtractError('redirect without Location header')
            url = urllib.parse.urljoin(url, location)
            continue
        with response:
            data = response.read() if max_bytes is None else response.read(max_bytes + 1)
            if max_bytes is not None and len(data) > max_bytes:
                raise ExtractError('scraping response exceeds the configured byte limit')
            path = urllib.parse.urlsplit(url).path
            name = urllib.parse.unquote(PurePosixPath(path).name)
            if classify(name) not in SUPPORTED | {'zip', 'tar'}:
                media = response.headers.get_content_type()
                name = {'text/html':'page.html','text/plain':'page.txt',
                        'application/json':'page.json','application/pdf':'page.pdf',
                        'application/zip':'archive.zip',
                        'application/vnd.openxmlformats-officedocument.wordprocessingml.document':'page.docx'}.get(media, '')
            if not name:
                raise ExtractError('unsupported URL type')
            return name, data, url
    raise ExtractError('too many redirects')

def import_one(name, raw, provenance, out):
    out = Path(out)
    accepted, rejected = [], []
    entries = iter_archive(name, raw) if classify(name) in {'zip', 'tar'} else [(name, raw)]
    for entry_name, blob in entries:
        try:
            if is_internal_file(PurePosixPath(entry_name).name):
                raise ExtractError('internal project metadata is not training content')
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

def collect_directory(root, *, used_sources=None, excluded_directory=None, scraping_cache=None):
    """Collect local files, direct 'url' sources and bounded 'scraping' sources.

    A URL list uses UTF-8, one URL per line; empty lines and # comments are ignored.
    Failures are recorded per line without interrupting other files or URLs.
    """
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise ExtractError('directory not found or symbolic link')
    root = root.resolve()
    used_sources = used_sources if used_sources is not None else set()
    excluded = Path(excluded_directory).resolve() if excluded_directory else None
    sources, rejected, seen_urls = [], [], set()
    scraping_cache = scraping_cache if scraping_cache is not None else {}
    for path in sorted(root.rglob('*')):
        resolved = path.resolve()
        if excluded and (resolved == excluded or excluded in resolved.parents):
            continue
        relative = path.relative_to(root)
        if any(part in SKIP_DIRS for part in relative.parts) or path.is_symlink():
            continue
        if not path.is_file():
            continue
        if is_internal_file(path.name):
            continue
        if path.name == 'scraping':
            from scraping import crawl
            try:
                lines = path.read_text(encoding='utf-8-sig').splitlines()
            except (UnicodeError, OSError) as exc:
                rejected.append({'source': str(path), 'reason': f'unreadable scraping list: {exc}'})
                continue
            for line_no, line in enumerate(lines, 1):
                seed = line.strip()
                if not seed or seed.startswith('#'):
                    continue
                pages, errors = crawl(seed, used_sources=used_sources,
                                      seen_urls=seen_urls, cache=scraping_cache)
                sources.extend(pages)
                rejected.extend({'source': f'{path}:{line_no}', **item} for item in errors)
            continue
        if path.name == 'url':
            try:
                lines = path.read_text(encoding='utf-8-sig').splitlines()
            except (UnicodeError, OSError) as exc:
                rejected.append({'source': str(path), 'reason': f'unreadable URL list: {exc}'})
                continue
            for line_no, raw_line in enumerate(lines, start=1):
                url = raw_line.strip()
                if not url or url.startswith('#'):
                    continue
                source = f'{path}:{line_no}'
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                source_key = f'url:{url}'
                if source_key in used_sources:
                    print(f'Skipping previously imported URL: {url}', flush=True)
                    continue
                try:
                    print(f'Importing URL: {url} (from {source})', flush=True)
                    name, raw, final_url = fetch_url(url)
                    sources.append((name, raw, final_url, source_key))
                except (ExtractError, ValueError, urllib.error.URLError, OSError) as exc:
                    rejected.append({'source': source, 'url': url, 'reason': str(exc)})
            continue
        if classify(path.name) not in SUPPORTED | {'zip', 'tar'}:
            continue
        try:
            data = path.read_bytes()
            source_key = f'file:{resolved}:{hashlib.sha256(data).hexdigest()}'
            if source_key in used_sources:
                print(f'Skipping previously imported file: {path}', flush=True)
                continue
            print(f'Importing file: {path}', flush=True)
            sources.append((path.name, data, str(path), source_key))
        except OSError as exc:
            rejected.append({'source': str(path), 'reason': str(exc)})
    return sources, rejected


def import_sources(sources, rejected, out, *, used_sources=None):
    output = Path(out)
    accepted = []
    used_sources = used_sources if used_sources is not None else set()
    for source in sources:
        name, data, provenance = source[:3]
        source_key = source[3] if len(source) > 3 else None
        if SENSITIVE.search(name):
            rejected.append({'source': provenance, 'reason': 'potentially sensitive name'})
            continue
        print(f'Extracting content: {provenance}', flush=True)
        try:
            imported, failed = import_one(name, data, provenance, output)
            accepted.extend(imported)
            rejected.extend([{'source': provenance, **item} for item in failed])
            if imported and source_key:
                used_sources.add(source_key)
                if urllib.parse.urlsplit(provenance).scheme in {'http', 'https'}:
                    used_sources.add(f'url:{provenance}')
        except (ExtractError, OSError) as exc:
            rejected.append({'source': provenance, 'reason': str(exc)})
    if accepted or rejected:
        output.mkdir(parents=True, exist_ok=True)
        (output / 'import_report.json').write_text(
            json.dumps({'accepted': accepted, 'rejected': rejected}, indent=2, ensure_ascii=False),
            encoding='utf-8')
    print(f'Imported: {len(accepted)}; rejected: {len(rejected)}', flush=True)
    for item in rejected[:SETTINGS["runtime"]["rejected_preview_limit"]]:
        print('REJECTED:', item, flush=True)
    state_path = output / 'import_state.json'
    if used_sources:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = state_path.with_suffix('.json.tmp')
        temporary_path.write_text(
            json.dumps({'used_sources': sorted(used_sources)}, indent=2), encoding='utf-8')
        temporary_path.replace(state_path)
    return accepted, rejected


def import_directory(root, out=SETTINGS["functions"]["import_directory"]["out"], *, force=False):
    output = Path(out).resolve()
    state_path = output / 'import_state.json'
    used_sources = set()
    if not force and state_path.is_file():
        try:
            used_sources.update(json.loads(state_path.read_text(encoding='utf-8')).get('used_sources', []))
        except (json.JSONDecodeError, OSError):
            print(f'Ignoring invalid import state: {state_path}', flush=True)
    if not force and output.is_dir():
        for report_path in output.rglob('import_report.json'):
            try:
                report = json.loads(report_path.read_text(encoding='utf-8'))
            except (json.JSONDecodeError, OSError):
                continue
            for entry in report.get('accepted', []):
                source = entry.get('source', '')
                if urllib.parse.urlsplit(source).scheme in {'http', 'https'}:
                    used_sources.add(f'url:{source}')
    scraping_path = output / 'scraping_state.json'
    scraping_cache = {}
    if not force and scraping_path.is_file():
        try:
            scraping_cache = json.loads(scraping_path.read_text(encoding='utf-8'))
            if not isinstance(scraping_cache, dict):
                raise ValueError('expected an object')
        except (ValueError, OSError):
            print(f'Ignoring invalid scraping state: {scraping_path}', flush=True)
            scraping_cache = {}
    sources, rejected = collect_directory(
        root, used_sources=used_sources, excluded_directory=output, scraping_cache=scraping_cache)
    result = import_sources(sources, rejected, output, used_sources=used_sources)
    if scraping_cache:
        output.mkdir(parents=True, exist_ok=True)
        temporary = scraping_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(scraping_cache, indent=2), encoding='utf-8')
        temporary.replace(scraping_path)
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description='Import local files, directories, URLs, ZIP archives, and TAR.GZ into the Vorpal corpus')
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--file')
    group.add_argument('--directory')
    group.add_argument('--url')
    p.add_argument('--out', default=SETTINGS["cli"]["out"])
    args = p.parse_args(argv)
    output = Path(args.out)
    sources = []
    rejected = []
    try:
        if args.url:
            print(f'Importing URL: {args.url}', flush=True)
            name, raw, url = fetch_url(args.url)
            sources = [(name, raw, url)]
        elif args.file:
            path = Path(args.file)
            if path.is_symlink() or not path.is_file():
                raise ExtractError('invalid file or symbolic link')
            print(f'Importing file: {path}', flush=True)
            sources = [(path.name, path.read_bytes(), str(path))]
        else:
            accepted, rejected = import_directory(args.directory, output, force=True)
        if not args.directory:
            accepted, rejected = import_sources(sources, rejected, output)
        if not accepted:
            p.exit(1, 'No files were imported.\n')
        print('Review the licenses and files in corpus/imported before preparing data/.')
        print('python src/data.py --corpus corpus --out data')
    except (ExtractError, OSError, ValueError, urllib.error.URLError) as exc:
        p.exit(1, f'Error: {exc}\n')

if __name__ == '__main__':
    main()
