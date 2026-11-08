"""Persistent document split assignments shared by the corpus training profiles."""

import json
import os
import re
from contextlib import contextmanager
from pathlib import Path


SPLIT_KEYS = {
    'train': 'train_document_hashes',
    'validation': 'valid_document_hashes',
    'test': 'test_document_hashes',
}
REGISTRY_NAME = 'shared_split_state.json'
LOCK_NAME = 'shared_split_state.lock'


def merge_splits(splits, other, source):
    conflicts = ((other['train'] & splits['validation']) | (other['train'] & splits['test'])
                 | (splits['train'] & other['validation']) | (splits['train'] & other['test'])
                 | (other['train'] & other['validation']) | (other['train'] & other['test']))
    if conflicts:
        hashes = ', '.join(sorted(conflicts)[:10])
        raise ValueError(f'Document split conflict in {source}: {len(conflicts)} hash(es) '
                         f'assigned to training and held out: {hashes}. Review split histories '
                         'and use a fresh holdout if a checkpoint has already seen these documents.')
    # Older profiles recorded official test documents as validation history.
    for split in SPLIT_KEYS:
        splits[split].update(other[split])
    splits['validation'].difference_update(splits['test'])


def read_splits(path):
    splits = {split: set() for split in SPLIT_KEYS}
    if not path.exists():
        return splits
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        for split, key in SPLIT_KEYS.items():
            hashes = value.get(key, [])
            if not isinstance(hashes, list) or any(
                not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
                for digest in hashes
            ):
                raise ValueError(f'invalid {key}')
            splits[split].update(hashes)
    except (OSError, ValueError, AttributeError) as exc:
        raise ValueError(f'Invalid dataset split state {path}: {exc}') from exc
    merge_splits(splits, splits, path)
    return splits


def write_splits(path, splits):
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps({
        key: sorted(splits[split]) for split, key in SPLIT_KEYS.items()
        if split != 'test' or splits[split]
    }, indent=2), encoding='utf-8')
    temporary.replace(path)


def profile_history(out):
    state = out / 'split_state.json'
    return read_splits(state if state.exists() else out / 'manifest.json')


def shared_history(root, outputs):
    path = root / REGISTRY_NAME
    splits = read_splits(path)
    histories = set(root.rglob('split_state.json'))
    histories.update(path for path in root.rglob('manifest.json')
                     if not (path.parent / 'split_state.json').exists())
    for out in outputs:
        state = out / 'split_state.json'
        histories.add(state if state.exists() else out / 'manifest.json')
    for history in sorted(histories):
        merge_splits(splits, read_splits(history), history)
    return splits


@contextmanager
def split_lock(root):
    """Serialize registry read/modify/write and binary publication."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / LOCK_NAME).open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield
