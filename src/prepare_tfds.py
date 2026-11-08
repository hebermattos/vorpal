"""Prepare reviewed TFDS text/instruction adapters; importing this module never downloads."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unicodedata

from config import configure_tensorflow_environment, load_config
from content_safety import SECRET_CONTENT
from prepare_wiki40b import FILENAMES, SPLITS

ADAPTERS = {
    'databricks_dolly:1.0.0': 'dolly',
    'squad/v2.0:3.0.0': 'squad',
    'natural_instructions:1.0.1': 'natural_instructions',
    'opus/en-pt_gnome:0.1.0': 'opus',
}
CATALOG = 'https://www.tensorflow.org/datasets/catalog/'


def field(value, *, empty=False):
    if isinstance(value, bytes):
        value = value.decode('utf-8')
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError('Expected a nonempty UTF-8 text field.')
    value = unicodedata.normalize('NFC', value.replace('\r\n', '\n').replace('\r', '\n')).strip()
    if '\ufffd' in value or any(ord(char) < 32 and char not in '\n\t' for char in value):
        raise ValueError('Invalid control/replacement character in text.')
    return value


def format_record(identifier, record, prompts):
    adapter = ADAPTERS[identifier]
    if adapter == 'dolly':
        values = {key: field(record[key], empty=(key == 'context')) for key in ('instruction', 'context', 'response')}
        group = values['instruction'] + '\n' + values['context']
    elif adapter == 'squad':
        values = {key: field(record[key]) for key in ('context', 'question')}
        answers = record['answers']['text']
        if bool(record['is_impossible']):
            values['answer'] = prompts['unanswerable']
        elif len(answers):
            values['answer'] = field(answers[0])
        else:
            raise ValueError('Answerable SQuAD example has no answer.')
        group = values['context']
    elif adapter == 'natural_instructions':
        values = {key: field(record[key], empty=(key == 'input')) for key in ('definition', 'input', 'output')}
        group = values['definition'] + '\n' + values['input']
    else:
        values = {key: field(record[key]) for key in ('en', 'pt')}
        group = values['en']
    # Check fields too: metadata must never conceal a sensitive input.
    if any(SECRET_CONTENT.search(value) for value in values.values()):
        raise ValueError('Possible secret in dataset record.')
    return prompts[adapter].format(**values), hashlib.sha256(group.encode('utf-8')).hexdigest()


def export_records(identifier, records, out, *, settings=None):
    """Atomically export official splits, or stable prompt-group splits for train-only data."""
    if identifier not in ADAPTERS:
        raise ValueError(f'Unsupported text adapter: {identifier}')
    settings = settings or load_config('tfds_text')
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise FileExistsError(f'Output already exists: {out}')
    if 'train' not in records or set(records) - set(SPLITS):
        raise ValueError('Expected train and optional official validation/test splits.')
    fallback = 'validation' not in records and 'test' not in records
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f'.{out.name}-', dir=out.parent))
    manifest = {'dataset': identifier, 'source': CATALOG + identifier.split(':')[0].split('/')[0],
                'split_policy': ('Stable SHA-256 prompt groups with configured validation fraction; not official splits.' if fallback else 'Official splits; held-out context groups and duplicate text take precedence.'),
                'preparation': settings, 'license_note': 'Retain upstream attribution. Dolly: CC BY-SA 3.0; other adapters: consult source/subset licenses before redistribution.',
                'splits': {}, 'rejected': {}}
    streams, indexes, stats = {}, {}, {}
    seen, held_groups = set(), set()
    try:
        for split in SPLITS:
            streams[split] = (staging / FILENAMES[split]).open('wb')
            indexes[split] = (staging / f'{split}.jsonl').open('w', encoding='utf-8')
            stats[split] = {'file': FILENAMES[split], 'documents': 0, 'bytes': 0}
        for source_split in SPLITS:
            if source_split not in records:
                continue
            rejected = Counter()
            for number, record in enumerate(records[source_split]):
                if number >= settings['runtime']['max_records_per_split']:
                    break
                try:
                    text, group = format_record(identifier, record, settings['prompts'])
                    if len(text) < settings['runtime']['min_text_length']:
                        raise ValueError('Text is shorter than the configured minimum.')
                    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
                    split = source_split
                    if fallback:
                        split = ('validation' if int(group, 16) / 2**256 < settings['runtime']['validation_fraction'] else 'train')
                    elif source_split == 'train' and group in held_groups:
                        rejected['held_out_group'] += 1
                        continue
                    if digest in seen:
                        rejected['duplicate_text'] += 1
                        continue
                    seen.add(digest)
                    if split != 'train':
                        held_groups.add(group)
                    payload = (text + '\n\n').encode('utf-8')
                    entry = {'document_id': digest, 'text_sha256': digest, 'group_sha256': group,
                             'source_split': source_split, 'offset': stats[split]['bytes'], 'bytes': len(payload)}
                    streams[split].write(payload)
                    indexes[split].write(json.dumps(entry) + '\n')
                    stats[split]['bytes'] += len(payload)
                    stats[split]['documents'] += 1
                except (ValueError, TypeError, KeyError, UnicodeError) as exc:
                    rejected[str(exc)] += 1
            manifest['rejected'][source_split] = dict(rejected)
        if not stats['train']['documents']:
            raise ValueError('No usable training records; adjust the source selection or record limit.')
        for split in SPLITS:
            streams[split].close()
            indexes[split].close()
            digest = hashlib.sha256()
            with (staging / FILENAMES[split]).open('rb') as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            manifest['splits'][split] = {**stats[split], 'sha256': digest.hexdigest()}
        (staging / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        (staging / 'LICENSE.txt').write_text(manifest['license_note'] + '\n' + manifest['source'] + '\n', encoding='utf-8')
        staging.replace(out)
    finally:
        for stream in [*streams.values(), *indexes.values()]:
            stream.close()
        if staging.exists():
            shutil.rmtree(staging)
    return manifest


def make_builder(tfds, identifier, directory):
    if identifier not in ADAPTERS:
        raise ValueError(f'Unsupported text adapter: {identifier}')
    if ADAPTERS[identifier] == 'opus':
        config = tfds.translate.opus.OpusConfig(name='en-pt_gnome', language_pair=('en', 'pt'),
                                               subsets=['GNOME'], version=tfds.core.Version('0.1.0'))
        return tfds.builder('opus', config=config, data_dir=directory)
    return tfds.builder(identifier, data_dir=directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', choices=tuple(ADAPTERS), required=True)
    parser.add_argument('--tfds-dir', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    if Path(args.out).exists():
        parser.error('Output already exists; reuse it or choose a new export path.')
    configure_tensorflow_environment()
    try:
        import tensorflow_datasets as tfds
    except ImportError:
        parser.error('Install dataset support: python -m pip install -r requirements-tfds.txt')
    builder = make_builder(tfds, args.dataset, args.tfds_dir)
    builder.download_and_prepare()
    def rows(split):
        dataset = builder.as_dataset(split=split, shuffle_files=False,
                                     read_config=tfds.ReadConfig(try_autocache=False))
        yield from tfds.as_numpy(dataset)
    records = {split: rows(split) for split in SPLITS if split in builder.info.splits}
    manifest = export_records(args.dataset, records, args.out)
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
