"""Prepare licensed local material, exclude known secret-bearing files, split by document."""

import argparse
import hashlib
import json
import re
from pathlib import Path
from content_safety import SECRET_CONTENT

try:
    from .config import load_config
except ImportError:  # Script invocation.
    from config import load_config

SETTINGS = load_config("data")

EXT = set(SETTINGS["policy"]["ext"])
SKIP_DIRS = set(SETTINGS["policy"]["skip_dirs"])
SKIP_NAME = re.compile(SETTINGS["policy"]["skip_name"], re.I)


def is_internal_file(name):
    return name in SETTINGS["policy"]["skip_files"]


def _split_documents(docs, out):
    """Persist content-hash assignments, including documents absent this run."""
    state_path = out / 'split_state.json'
    previous_path = state_path if state_path.exists() else out / 'manifest.json'
    previous = {}
    if previous_path.exists():
        try:
            previous = json.loads(previous_path.read_text(encoding='utf-8'))
            for key in ('train_document_hashes', 'valid_document_hashes'):
                values = previous.get(key, [])
                if not isinstance(values, list) or any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value) for value in values):
                    raise ValueError(f'invalid {key}')
            train_hashes = set(previous.get('train_document_hashes', []))
            valid_hashes = set(previous.get('valid_document_hashes', []))
            if train_hashes & valid_hashes:
                raise ValueError('document hashes overlap between train and validation')
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise ValueError(f'Invalid dataset split state {previous_path}: {exc}') from exc
    else:
        train_hashes, valid_hashes = set(), set()
    digest = lambda doc: hashlib.sha256(doc[1].encode('utf-8')).hexdigest()
    docs.sort(key=digest)
    train = [doc for doc in docs if digest(doc) in train_hashes]
    valid = [doc for doc in docs if digest(doc) in valid_hashes]
    new = [doc for doc in docs if digest(doc) not in train_hashes | valid_hashes]
    desired = max(1, round(len(docs) * SETTINGS['runtime']['validation_fraction'])) if len(docs) > 1 else 0
    # Assign only new hashes: an existing validation example never becomes training.
    count = min(max(0, desired - len(valid)), max(0, len(new) - (0 if train else 1)))
    valid.extend(new[:count])
    train.extend(new[count:])
    train.sort(key=digest)
    valid.sort(key=digest)
    train_hashes.update(digest(doc) for doc in train)
    valid_hashes.update(digest(doc) for doc in valid)
    out.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps({
        'train_document_hashes': sorted(train_hashes),
        'valid_document_hashes': sorted(valid_hashes),
    }, indent=2), encoding='utf-8')
    temporary.replace(state_path)
    return train, valid

def prepare(root, out, *, exclude_hashes=()):
    root, out = Path(root).resolve(), Path(out)
    excluded = set(exclude_hashes)
    docs, seen, rejected = [], set(), []
    skipped_internal, duplicates = [], []
    output_root = out.resolve()
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in EXT: continue
        if is_internal_file(path.name):
            skipped_internal.append(str(path.relative_to(root))); continue
        if output_root == path.resolve() or output_root in path.resolve().parents: continue
        if any(x in SKIP_DIRS for x in path.relative_to(root).parts[:-1]) or SKIP_NAME.search(path.name): continue
        try: text = path.read_text('utf-8')
        except (UnicodeError, OSError): continue
        if SECRET_CONTENT.search(text):
            rejected.append(str(path.relative_to(root))); continue
        if len(text.strip()) < SETTINGS["runtime"]["min_text_length"]: continue
        digest = hashlib.sha256(text.encode()).hexdigest()
        # Keep existing document IDs, but collapse importer-added trailing newlines.
        duplicate_key = hashlib.sha256(text.rstrip().encode('utf-8')).hexdigest()
        if duplicate_key in seen:
            duplicates.append(str(path.relative_to(root))); continue
        seen.add(duplicate_key)
        if digest in excluded: continue
        docs.append((str(path.relative_to(root)), text))
    if not docs and not excluded:
        raise SystemExit('No valid documents found. Add .py/.md/.txt files under corpus/.')
    train, valid = _split_documents(docs, out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'train.bin').write_bytes(b'\n\n'.join(s.encode('utf8') for _, s in train))
    (out / 'valid.bin').write_bytes(b'\n\n'.join(s.encode('utf8') for _, s in valid))
    manifest = {'train_documents': [n for n,_ in train], 'valid_documents': [n for n,_ in valid],
                'train_document_hashes': [hashlib.sha256(s.encode()).hexdigest() for _, s in train],
                'valid_document_hashes': [hashlib.sha256(s.encode()).hexdigest() for _, s in valid],
                'skipped_for_secrets': rejected,
                'skipped_internal_files': skipped_internal,
                'skipped_duplicates': duplicates,
                'train_bytes': (out/'train.bin').stat().st_size, 'valid_bytes': (out/'valid.bin').stat().st_size}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf8')
    print(json.dumps(manifest, indent=2, ensure_ascii=False))

if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--corpus', default=SETTINGS["cli"]["corpus"]); p.add_argument('--out', default=SETTINGS["cli"]["out"]); a = p.parse_args()
    prepare(a.corpus, a.out)
