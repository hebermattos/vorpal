"""Mix official Wiki40B training articles and previously split local teaching text."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import shutil

try:
    from .prepare_wiki40b import clean_text
    from .content_safety import SECRET_CONTENT
except ImportError:
    from prepare_wiki40b import clean_text
    from content_safety import SECRET_CONTENT


def mix_training(wiki, corpus, split_state, out, *, articles=1000):
    if type(articles) is not int or articles < 1:
        raise ValueError('articles must be a positive integer')
    wiki, corpus, out = Path(wiki), Path(corpus), Path(out)
    if out.exists() or out.is_symlink():
        raise FileExistsError(f'Output already exists: {out}')
    state = json.loads(Path(split_state).read_text(encoding='utf-8'))
    known_train, known_valid = set(state['train_document_hashes']), set(state['valid_document_hashes'])
    if known_train & known_valid:
        raise ValueError('Local training and validation hashes overlap.')
    held_out = set()
    for split in ('validation', 'test'):
        with (wiki / f'{split}.jsonl').open(encoding='utf-8') as source:
            held_out.update(json.loads(line)['text_sha256'] for line in source)
    files = sorted([*corpus.glob('qa_*.txt'), *corpus.glob('texto_*.txt'), *corpus.glob('linguagem/*.txt')])
    local, local_valid, selected = bytearray(), bytearray(), []
    for path in files:
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        text = payload.decode('utf-8')
        if digest in known_valid:
            if not SECRET_CONTENT.search(text):
                local_valid.extend(payload.rstrip() + b'\n\n')
            continue
        if digest not in known_train:
            continue
        normalized_hash = hashlib.sha256(clean_text(text).encode('utf-8')).hexdigest()
        if SECRET_CONTENT.search(text) or normalized_hash in held_out:
            continue
        local.extend(payload.rstrip() + b'\n\n')
        selected.append({'file': str(path), 'sha256': digest})
    if not selected:
        raise ValueError('No eligible local teaching documents in the persisted training split.')
    wiki_payload = bytearray()
    wiki_entries = []
    with (wiki / 'train.bin').open('rb') as binary, (wiki / 'train.jsonl').open(encoding='utf-8') as index:
        for line in index:
            entry = json.loads(line)
            if type(entry['offset']) is not int or type(entry['bytes']) is not int or entry['offset'] < 0 or entry['bytes'] < 1:
                raise ValueError('Invalid article offset or length.')
            binary.seek(entry['offset'])
            payload = binary.read(entry['bytes'])
            normalized = payload.decode('utf-8').rstrip('\n')
            digest = hashlib.sha256(normalized.encode('utf-8')).hexdigest()
            if len(payload) != entry['bytes'] or digest != entry['text_sha256'] or digest in held_out:
                raise ValueError('Wiki training record failed integrity/held-out checks.')
            if SECRET_CONTENT.search(normalized):
                raise ValueError('Sensitive content in Wiki training record.')
            wiki_payload.extend(payload)
            wiki_entries.append({'wikidata_id': entry['wikidata_id'], 'text_sha256': digest})
            if len(wiki_entries) == articles:
                break
    if not wiki_entries:
        raise ValueError('No Wiki40B training records.')
    repetitions = max(1, round(len(wiki_payload) / len(local)))
    replay = local * repetitions
    payload = wiki_payload + replay
    manifest = {'description': 'Language + local teaching replay; approximately equal byte weights. Repetition is replay, not new data.',
                'wiki_directory': str(wiki), 'local_split_state': str(split_state),
                'wiki_articles': wiki_entries, 'local_documents': selected,
                'local_repetitions': repetitions, 'wiki_bytes': len(wiki_payload),
                'local_bytes': len(replay), 'bytes': len(payload),
                'local_validation_bytes': len(local_valid),
                'local_validation_sha256': hashlib.sha256(local_valid).hexdigest(),
                'sha256': hashlib.sha256(payload).hexdigest(),
                'held_out_policy': 'Only official Wiki train and persisted local train; no validation/test payloads are included.'}
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f'.{out.name}-', dir=out.parent))
    try:
        (staging / 'train.bin').write_bytes(payload)
        (staging / 'valid_local.bin').write_bytes(local_valid)
        (staging / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        staging.replace(out)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wiki', default='data/wiki40b/pt')
    parser.add_argument('--corpus', default='corpus/pt_br')
    parser.add_argument('--split-state', default='data/pt_br/split_state.json')
    parser.add_argument('--out', default='data/wiki40b/pt-mixed')
    parser.add_argument('--articles', type=int, default=1000)
    args = parser.parse_args()
    result = mix_training(args.wiki, args.corpus, args.split_state, args.out, articles=args.articles)
    print(f'Mixed {len(result["wiki_articles"])} Wiki train articles with {len(result["local_documents"])} local teaching documents.')
    print(f'Wiki: {result["wiki_bytes"]:,} bytes; local replay: {result["local_bytes"]:,} bytes.')


if __name__ == '__main__':
    main()
