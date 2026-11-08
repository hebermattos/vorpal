"""Prepare licensed local material, exclude known secret-bearing files, split by document."""

import argparse
import hashlib
import json
import re
from pathlib import Path
from content_safety import SECRET_CONTENT

try:
    from .config import load_config
    from .split_registry import (LOCK_NAME, REGISTRY_NAME, merge_splits, profile_history,
                                 read_splits, shared_history, split_lock, write_splits)
except ImportError:  # Script invocation.
    from config import load_config
    from split_registry import (LOCK_NAME, REGISTRY_NAME, merge_splits, profile_history,
                                read_splits, shared_history, split_lock, write_splits)

SETTINGS = load_config("data")

EXT = set(SETTINGS["policy"]["ext"])
SKIP_DIRS = set(SETTINGS["policy"]["skip_dirs"])
SKIP_NAME = re.compile(SETTINGS["policy"]["skip_name"], re.I)


def is_internal_file(name):
    return name in SETTINGS["policy"]["skip_files"] or name in {REGISTRY_NAME, LOCK_NAME}


def default_split_root():
    return Path(SETTINGS["cli"]["out"]).resolve()


def configured_outputs(root, out):
    outputs = {out}
    if root == default_split_root():
        outputs.update(Path(profile['out']).resolve() for profile in load_config('web')['profiles'])
    return outputs


def _split_history(out):
    splits = profile_history(out)
    return splits['train'], splits['validation'] | splits['test']


def register_prepared_training(path, split_root=None):
    """Verify and register managed prepared inputs before they reach the model."""
    path = Path(path).resolve()
    root = Path(split_root).resolve() if split_root is not None else default_split_root()
    manifest_path = path.with_name('manifest.json')
    if path.name in {'valid.bin', 'validation.bin', 'test.bin', 'valid_local.bin'}:
        raise ValueError(f'Cannot use a held-out binary for training: {path}')
    if path.name != 'train.bin':
        return
    if not manifest_path.is_file():
        if path.with_name('split_state.json').exists():
            raise ValueError(f'Prepared training manifest is missing: {path}. Prepare it again.')
        # Raw byte streams have no per-document provenance.
        return
    with split_lock(root):
        splits = shared_history(root, configured_outputs(root, path.parent))
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        assignments = {split: set() for split in splits}
        if 'train_document_hashes' in manifest:
            assignments = read_splits(manifest_path)
            size, checksum = manifest.get('train_bytes'), manifest.get('train_sha256')
        elif 'dataset' in manifest and 'splits' in manifest:
            from tfds_sources import documents
            for _, text, split in documents([{'dataset': manifest['dataset'], 'out': str(path.parent)}]):
                assignments[split].update((_digest(text), _digest(text.rstrip())))
            size, checksum = manifest['splits']['train']['bytes'], manifest['splits']['train']['sha256']
        elif 'wiki_articles' in manifest and 'local_documents' in manifest:
            assignments['train'].update(item['text_sha256'] for item in manifest['wiki_articles'])
            assignments['train'].update(item['sha256'] for item in manifest['local_documents'])
            size, checksum = manifest['bytes'], manifest['sha256']
        else:
            return
        if path.stat().st_size != size:
            raise ValueError(f'Prepared training binary differs from its manifest: {path}. Prepare it again.')
        if checksum is not None:
            with path.open('rb') as binary:
                if hashlib.file_digest(binary, 'sha256').hexdigest() != checksum:
                    raise ValueError(f'Prepared training binary differs from its manifest: {path}. Prepare it again.')
        merge_splits(splits, assignments, manifest_path)
        write_splits(root / REGISTRY_NAME, splits)


def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _split_documents(docs, splits):
    """Assign new content only. Known documents retain their split across profiles."""
    docs.sort(key=lambda doc: _digest(doc[1]))
    grouped = {split: [] for split in (*splits, None)}
    for doc in docs:
        raw, canonical = _digest(doc[1]), _digest(doc[1].rstrip())
        split = next((kind for kind, hashes in splits.items() if raw in hashes or canonical in hashes), None)
        grouped[split].append(doc)
    train, valid, new = grouped['train'], grouped['validation'], grouped[None]
    eligible = len(train) + len(valid) + len(new)
    desired = max(1, round(eligible * SETTINGS['runtime']['validation_fraction'])) if eligible > 1 else 0
    count = min(max(0, desired - len(valid)), max(0, len(new) - (0 if train else 1)))
    valid.extend(new[:count])
    train.extend(new[count:])
    train.sort(key=lambda doc: _digest(doc[1]))
    valid.sort(key=lambda doc: _digest(doc[1]))
    return train, valid, grouped['test']


def prepare(root, out, *, exclude_hashes=(), external_documents=(), split_root=None):
    root, out = Path(root).resolve(), Path(out).resolve()
    if split_root is None and out.is_relative_to(default_split_root()):
        split_root = default_split_root()
    lock_root = Path(split_root).resolve() if split_root is not None else out
    with split_lock(lock_root):
        profile = profile_history(out)
        shared = shared_history(lock_root, configured_outputs(lock_root, out)) if split_root is not None else None
        return _prepare(root, out, set(exclude_hashes), external_documents, profile, shared, lock_root)


def _prepare(root, out, excluded, external_documents, profile, shared, lock_root):
    candidates, omitted, rejected = {}, set(), []
    splits = shared if shared is not None else {split: hashes.copy() for split, hashes in profile.items()}
    known_train = splits['train']
    known_valid = splits['validation'] | splits['test']
    skipped_internal, duplicates = [], []
    assignments = {split: set() for split in splits}
    official, aliases = {}, {}
    # External adapters yield held-out documents first, so official splits win
    # over duplicate training records and local copies of held-out text.
    from prepare_wiki40b import clean_text
    for name, text, split in external_documents:
        if split not in ('train', 'validation', 'test'):
            raise ValueError(f'Invalid external split: {split}')
        try:
            normalized = clean_text(text)
        except ValueError:
            # Non-Wiki adapters may legitimately quote marker-like code/text.
            normalized = text.rstrip()
        key = hashlib.sha256(normalized.encode('utf-8')).hexdigest()
        if key in official:
            continue
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        official[key] = split
        canonical = _digest(text.rstrip())
        if digest != canonical:
            aliases.setdefault(canonical, set()).add(digest)
        assignments[split].add(digest)
        if SECRET_CONTENT.search(text):
            rejected.append(name)
            continue
        if split != 'test' and digest not in excluded:
            candidates[key] = (name, text, digest)
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in EXT: continue
        if is_internal_file(path.name):
            skipped_internal.append(str(path.relative_to(root))); continue
        if any(directory in path.resolve().parents for directory in (out, lock_root) if directory != root): continue
        if any(x in SKIP_DIRS for x in path.relative_to(root).parts[:-1]) or SKIP_NAME.search(path.name): continue
        try: text = path.read_text('utf-8')
        except (UnicodeError, OSError): continue
        if SECRET_CONTENT.search(text):
            rejected.append(str(path.relative_to(root))); continue
        if len(text.strip()) < SETTINGS["runtime"]["min_text_length"]: continue
        digest = _digest(text)
        duplicate_key = _digest(text.rstrip())
        if digest != duplicate_key:
            aliases.setdefault(duplicate_key, set()).add(digest)
        if official:
            try:
                official_key = hashlib.sha256(clean_text(text).encode('utf-8')).hexdigest()
            except ValueError:
                official_key = hashlib.sha256(text.rstrip().encode('utf-8')).hexdigest()
            if official_key in official:
                split = official[official_key]
                if split != 'train' and digest in known_train:
                    raise ValueError(f'Local training history overlaps official TFDS held-out text: {path} ({digest})')
                if split == 'train' and digest in known_valid:
                    raise ValueError(f'Official TFDS training text overlaps local held-out history: {path} ({digest})')
                duplicates.append(str(path.relative_to(root)))
                continue
        # Keep existing document IDs, but collapse importer-added trailing newlines.
        candidate = (str(path.relative_to(root)), text, digest)
        if digest in excluded:
            omitted.add(duplicate_key)
        previous = candidates.get(duplicate_key)
        if previous is not None:
            rank = lambda item: (item[2] in profile['validation'], item[2] in profile['train'],
                                 item[2] in known_valid, item[2] in known_train)
            if rank(candidate) > rank(previous):
                duplicates.append(previous[0])
                candidates[duplicate_key] = candidate
            else:
                duplicates.append(candidate[0])
        else:
            candidates[duplicate_key] = candidate
    docs = [(name, text) for key, (name, text, _) in candidates.items() if key not in omitted]
    if not docs and not excluded and not any(assignments.values()):
        raise SystemExit('No valid documents found. Add .py/.md/.txt files under corpus/.')
    merge_splits(splits, assignments, 'official TFDS splits')
    for canonical, raw_hashes in aliases.items():
        identities = raw_hashes | {canonical}
        known = {split for split, hashes in splits.items() if not hashes.isdisjoint(identities)}
        if 'train' in known and known - {'train'}:
            hashes = ', '.join(sorted(identities))
            raise ValueError(f'Document split conflict for equivalent content: {hashes}')
        for split in ('test', 'validation', 'train'):
            if split in known:
                splits[split].update(identities)
                if split == 'test':
                    splits['validation'].difference_update(identities)
                break
    train, valid, test = _split_documents(docs, splits)
    for split, documents in (('train', train), ('validation', valid), ('test', test)):
        for _, text in documents:
            assignments[split].add(_digest(text))
            canonical = _digest(text.rstrip())
            splits[split].update(aliases.get(canonical, set()) | {canonical, _digest(text)})
    merge_splits(profile, assignments, out)
    if shared is not None:
        write_splits(lock_root / REGISTRY_NAME, shared)
    out.mkdir(parents=True, exist_ok=True)
    write_splits(out / 'split_state.json', profile)
    binary_metadata = {}
    for kind, documents in (('train', train), ('valid', valid)):
        binary = b'\n\n'.join(text.encode('utf-8') for _, text in documents)
        temporary = out / f'{kind}.bin.tmp'
        temporary.write_bytes(binary)
        temporary.replace(out / f'{kind}.bin')
        binary_metadata.update({f'{kind}_bytes': len(binary),
                                f'{kind}_sha256': hashlib.sha256(binary).hexdigest()})
    manifest = {'train_documents': [n for n,_ in train], 'valid_documents': [n for n,_ in valid],
                'train_document_hashes': [hashlib.sha256(s.encode()).hexdigest() for _, s in train],
                'valid_document_hashes': [hashlib.sha256(s.encode()).hexdigest() for _, s in valid],
                'skipped_for_secrets': rejected,
                'skipped_internal_files': skipped_internal,
                'skipped_duplicates': duplicates,
                **binary_metadata}
    temporary = out / 'manifest.json.tmp'
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf8')
    temporary.replace(out / 'manifest.json')
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest

if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--corpus', default=SETTINGS["cli"]["corpus"]); p.add_argument('--out', default=SETTINGS["cli"]["out"]); a = p.parse_args()
    prepare(a.corpus, a.out, split_root=default_split_root())
