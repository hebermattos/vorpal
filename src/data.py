"""Prepare licensed local material, exclude known secret-bearing files, split by document."""
import argparse
import hashlib
import json
import re
from pathlib import Path

EXT = {'.py', '.md', '.txt', '.json', '.yml', '.yaml', '.sh'}
SKIP_DIRS = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', 'dist', 'build', 'checkpoints'}
SKIP_NAME = re.compile(r'(\.env|secret|credential|private|id_rsa|\.pem$|\.key$|token|password)', re.I)
SECRET = re.compile(r'(-----BEGIN [A-Z ]*PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|(?i:api[_-]?key|password|secret|token)\s*[:=]\s*["\']?[^\s"\']{8,})')

def prepare(root, out, limit_mb=8):
    root, out = Path(root).resolve(), Path(out)
    docs, seen, rejected = [], set(), []
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in EXT: continue
        if path.name == 'import_report.json': continue
        if any(x in SKIP_DIRS for x in path.relative_to(root).parts[:-1]) or SKIP_NAME.search(path.name): continue
        if path.stat().st_size > limit_mb * 1024**2: continue
        try: text = path.read_text('utf-8')
        except (UnicodeError, OSError): continue
        if SECRET.search(text):
            rejected.append(str(path.relative_to(root))); continue
        if len(text.strip()) < 50: continue
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest in seen: continue
        seen.add(digest)
        docs.append((str(path.relative_to(root)), text))
    if not docs: raise SystemExit('Nenhum documento válido. Adicione arquivos .py/.md/.txt em corpus/.')
    # Split at document boundaries to reduce train/validation leakage.
    docs.sort(key=lambda pair: hashlib.sha256(pair[0].encode()).hexdigest())
    valid_count = max(1, int(round(len(docs) * .1))) if len(docs) > 1 else 0
    valid = docs[:valid_count]; train = docs[valid_count:]
    out.mkdir(parents=True, exist_ok=True)
    (out / 'train.bin').write_bytes(b'\n\n'.join(s.encode('utf8') for _, s in train))
    (out / 'valid.bin').write_bytes(b'\n\n'.join(s.encode('utf8') for _, s in valid))
    manifest = {'train_documents': [n for n,_ in train], 'valid_documents': [n for n,_ in valid], 'skipped_for_secrets': rejected,
                'train_bytes': (out/'train.bin').stat().st_size, 'valid_bytes': (out/'valid.bin').stat().st_size}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf8')
    print(json.dumps(manifest, indent=2, ensure_ascii=False))

if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--corpus', default='corpus'); p.add_argument('--out', default='data'); a = p.parse_args()
    prepare(a.corpus, a.out)
