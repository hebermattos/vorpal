"""Discover reviewed TFDS adapters and stream their verified official documents."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from config import load_config
from data import SKIP_DIRS
from prepare_wiki40b import DATASET, FILENAMES, SPLITS
from prepare_tfds import ADAPTERS


def discover_sources(root):
    settings = load_config('tfds')
    supported = {item['dataset']: item for item in settings['sources']}
    found = {}
    root = Path(root)
    for path in sorted(root.rglob(settings['source_file'])):
        relative = path.relative_to(root)
        if (path.is_symlink() or not path.is_file()
                or any(part in SKIP_DIRS or part == 'imported' for part in relative.parts[:-1])
                or any(parent.is_symlink() for parent in path.parents)):
            continue
        for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
            identifier = line.strip()
            if not identifier or identifier.startswith('#'):
                continue
            if identifier not in supported or identifier not in {DATASET, *ADAPTERS}:
                raise ValueError(f'{path}:{number}: unsupported TFDS dataset {identifier!r}. '
                                 'A reviewed text/split adapter is required.')
            found[identifier] = supported[identifier]
    return list(found.values())


def ensure_dataset(source):
    settings = load_config('tfds')
    out = Path(source['out'])
    if not out.exists():
        print(f'Preparing TFDS dataset: {source["dataset"]}', flush=True)
        if source['dataset'] == DATASET:
            command = [sys.executable, str(Path(__file__).with_name('prepare_wiki40b.py'))]
        elif source['dataset'] in ADAPTERS:
            command = [sys.executable, str(Path(__file__).with_name('prepare_tfds.py')),
                       '--dataset', source['dataset']]
        else:
            raise ValueError(f'Unsupported TFDS dataset: {source["dataset"]}')
        subprocess.run([*command, '--tfds-dir', settings['download_dir'], '--out', str(out)], check=True)
    else:
        print(f'Reusing TFDS dataset: {source["dataset"]} ({out})', flush=True)
    manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('dataset') != source['dataset']:
        raise ValueError(f'TFDS dataset identity mismatch: {out}')
    if source['dataset'] in ADAPTERS and manifest.get('preparation') != load_config('tfds_text'):
        raise ValueError(f'TFDS preparation settings changed: {out}. Choose a new export path in config/tfds.json to preserve the existing cache.')
    for split in SPLITS:
        digest = hashlib.sha256()
        binary = out / FILENAMES[split]
        with binary.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        entry = manifest['splits'][split]
        if binary.stat().st_size != entry['bytes'] or digest.hexdigest() != entry['sha256']:
            raise ValueError(f'TFDS cache integrity check failed: {binary}. Restore or remove the cache to prepare it again.')
    return out


def documents(sources):
    """Yield official test, validation and train documents; never resplit them."""
    prepared = [(source, ensure_dataset(source)) for source in sources]
    for split in SPLITS:
        for source, out in prepared:
            offset = 0
            with (out / FILENAMES[split]).open('rb') as binary, (out / f'{split}.jsonl').open(encoding='utf-8') as index:
                for line in index:
                    item = json.loads(line)
                    size = item['bytes']
                    if type(size) is not int or size < 1 or type(item['offset']) is not int or item['offset'] != offset:
                        raise ValueError(f'Invalid TFDS record offset/length in {out}/{split}.jsonl')
                    payload = binary.read(size)
                    text = payload.decode('utf-8').rstrip('\n')
                    if len(payload) != size or hashlib.sha256(text.encode('utf-8')).hexdigest() != item['text_sha256']:
                        raise ValueError(f'TFDS record integrity check failed: {out}/{split}.jsonl')
                    offset += size
                    identifier = item.get('document_id', item.get('wikidata_id'))
                    if not isinstance(identifier, str) or not identifier:
                        raise ValueError(f'Missing TFDS document identifier: {out}/{split}.jsonl')
                    yield f'tfds:{source["dataset"]}/{split}/{identifier}', text, split
                if binary.read(1):
                    raise ValueError(f'Incomplete TFDS record index: {out}/{split}.jsonl')
