"""Export official Wiki40B Portuguese splits to Vorpal byte datasets (no model)."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata
import urllib.request
from urllib.parse import quote, urlencode

try:
    from .content_safety import SECRET_CONTENT
    from .config import configure_tensorflow_environment
except ImportError:
    from content_safety import SECRET_CONTENT
    from config import configure_tensorflow_environment

DATASET = 'wiki40b/pt:1.3.0'
LICENSE = 'CC-BY-SA-3.0'
CATALOG = 'https://www.tensorflow.org/datasets/catalog/wiki40b#description'
SPLITS = ('test', 'validation', 'train')  # Held-out IDs/hashes always take precedence.
FILENAMES = {'train': 'train.bin', 'validation': 'valid.bin', 'test': 'test.bin'}


def clean_text(value):
    if isinstance(value, bytes):
        value = value.decode('utf-8')
    if not isinstance(value, str):
        raise ValueError('Wiki40B text must be UTF-8 text.')
    value = unicodedata.normalize('NFC', value.replace('\r\n', '\n').replace('\r', '\n'))
    for marker in ('_START_ARTICLE_', '_START_SECTION_', '_START_PARAGRAPH_'):
        value = value.replace(marker, '\n\n')
    value = value.replace('_NEWLINE_', '\n')
    if re.search(r'_(?:START|END)_[A-Z_]+_', value):
        raise ValueError('Unknown Wiki40B structural marker.')
    value = '\n'.join(' '.join(line.split()) for line in value.split('\n'))
    return re.sub(r'\n{3,}', '\n\n', value).strip()


def export_records(records, out, *, min_chars=50):
    """Keep official split membership; exclude held-out IDs and exact text from train."""
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise FileExistsError(f'Output already exists: {out}. Choose a new directory; existing datasets are preserved.')
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f'.{out.name}-', dir=out.parent))
    manifest = {'dataset': DATASET, 'source': CATALOG, 'license': LICENSE,
                'license_url': 'https://creativecommons.org/licenses/by-sa/3.0/',
                'created_at': datetime.now(timezone.utc).isoformat(),
                'normalization': 'NFC; remove Wiki40B structural markers; unwrap whitespace; retain headings and accents.',
                'split_policy': 'Official splits; test then validation then train priority for duplicate entity IDs or normalized text hashes.',
                'secret_filter': 'Shared heuristic content_safety filter; not a guarantee of safety.', 'splits': {}}
    seen_ids, seen_hashes = set(), set()
    try:
        for split in SPLITS:
            rejected = Counter()
            accepted = total_bytes = processed = 0
            digest = hashlib.sha256()
            with (staging / FILENAMES[split]).open('wb') as binary, (staging / f'{split}.jsonl').open('w', encoding='utf-8') as index:
                for record in records[split]:
                    processed += 1
                    try:
                        identifier = record['wikidata_id']
                        if isinstance(identifier, bytes):
                            identifier = identifier.decode('utf-8')
                        if not isinstance(identifier, str) or not re.fullmatch(r'Q[0-9]+', identifier):
                            raise ValueError('Invalid Wikidata ID.')
                        if identifier in seen_ids:
                            rejected['duplicate_entity'] += 1
                            continue
                        seen_ids.add(identifier)
                        text = clean_text(record['text'])
                        version = record.get('version_id', '')
                        if isinstance(version, bytes):
                            version = version.decode('utf-8')
                        if not isinstance(version, str):
                            raise ValueError('Invalid version ID.')
                    except (KeyError, TypeError, ValueError, UnicodeError):
                        rejected['invalid_record'] += 1
                        continue
                    if len(text) < min_chars:
                        rejected['short_text'] += 1
                        continue
                    if '\ufffd' in text or re.search(r'[\x00-\x09\x0b-\x1f\x7f]', text):
                        rejected['invalid_text'] += 1
                        continue
                    if SECRET_CONTENT.search(text):
                        rejected['secret_content'] += 1
                        continue
                    payload = (text + '\n\n').encode('utf-8')
                    text_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
                    if text_hash in seen_hashes:
                        rejected['duplicate_text'] += 1
                        continue
                    seen_hashes.add(text_hash)
                    title = text.split('\n', 1)[0]
                    index.write(json.dumps({'wikidata_id': identifier, 'version_id': version,
                                            'title': title, 'source': f'https://pt.wikipedia.org/wiki/{quote(title, safe="")}',
                                            'text_sha256': text_hash, 'offset': total_bytes,
                                            'bytes': len(payload)}, ensure_ascii=False) + '\n')
                    binary.write(payload)
                    digest.update(payload)
                    accepted += 1
                    total_bytes += len(payload)
                    if accepted % 10000 == 0:
                        print(f'{split}: {accepted:,} accepted; {total_bytes / 1e6:.1f} MB', flush=True)
            if not accepted:
                raise ValueError(f'No usable records in official {split} split.')
            manifest['splits'][split] = {'file': FILENAMES[split], 'processed': processed,
                                        'documents': accepted, 'bytes': total_bytes,
                                        'sha256': digest.hexdigest(), 'rejected': dict(rejected)}
            print(f'{split}: {accepted:,}/{processed:,} documents; {dict(rejected)}', flush=True)
        (staging / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (staging / 'LICENSE.txt').write_text(
            'Wiki40B Portuguese, from Portuguese Wikipedia. License: CC BY-SA 3.0.\n'
            'https://creativecommons.org/licenses/by-sa/3.0/\n'
            f'{CATALOG}\nPer-article source titles, URLs, entity IDs and version IDs are recorded in the split JSONL indexes.\n'
            'Text was normalized and structural markers removed; see manifest.json.\n', encoding='utf-8')
        staging.replace(out)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return manifest



def download_prepared(tfds_dir):
    """Fetch public prepared TFDS shards over HTTPS, with GCS checksums.

    TFDS 4.9.10 disables its GCS helper. HTTPS avoids Apache Beam regeneration
    and preserves the official dataset metadata and shard membership.
    """
    prefix = 'datasets/wiki40b/pt/1.3.0/'
    listing_url = 'https://storage.googleapis.com/storage/v1/b/tfds-data/o?' + urlencode(
        {'prefix': prefix, 'maxResults': 32})
    with urllib.request.urlopen(listing_url, timeout=60) as response:
        listing = json.load(response)
    if listing.get('nextPageToken'):
        raise ValueError('Unexpected Wiki40B source listing; refusing an incomplete download.')
    objects = listing.get('items', [])
    expected = {'dataset_info.json', 'LICENSE'}
    expected.update(f'wiki40b-train.tfrecord-{i:05d}-of-00008' for i in range(8))
    expected.update(f'wiki40b-{split}.tfrecord-00000-of-00001' for split in ('test', 'validation'))
    names = {item['name'][len(prefix):] for item in objects if item['name'].startswith(prefix)}
    if names != expected or len(objects) != len(expected):
        raise ValueError('Unexpected Wiki40B source files; verify the official dataset version.')
    directory = Path(tfds_dir) / 'wiki40b/pt/1.3.0'
    directory.mkdir(parents=True, exist_ok=True)

    def fetch(item):
        filename = item['name'][len(prefix):]
        target = directory / filename
        url = 'https://storage.googleapis.com/tfds-data/' + quote(item['name'], safe='/') + '?' + urlencode({'generation': item['generation']})
        md5, sha256 = hashlib.md5(usedforsecurity=False), hashlib.sha256()
        temporary = target.with_suffix(target.suffix + '.part')
        existed = target.exists()
        if existed:
            stream = target.open('rb')
        else:
            stream = urllib.request.urlopen(url, timeout=120)
        size = 0
        try:
            with stream, (temporary.open('wb') if not existed else open(os.devnull, 'wb')) as output:
                while chunk := stream.read(1024 * 1024):
                    md5.update(chunk)
                    sha256.update(chunk)
                    size += len(chunk)
                    output.write(chunk)
            if size != int(item['size']) or base64.b64encode(md5.digest()).decode() != item['md5Hash']:
                raise ValueError(f'Source size/checksum mismatch: {filename}')
            if not existed:
                temporary.replace(target)
        except Exception:
            if not existed:
                temporary.unlink(missing_ok=True)
            raise
        print(f'Verified source: {filename} ({size / 1e6:.1f} MB)', flush=True)
        return {'file': filename, 'url': url, 'size': size, 'sha256': sha256.hexdigest(),
                'gcs_md5': item['md5Hash'], 'generation': item['generation']}

    with ThreadPoolExecutor(max_workers=4) as executor:
        sources = list(executor.map(fetch, objects))
    (directory / 'source_downloads.json').write_text(json.dumps(sources, indent=2) + '\n', encoding='utf-8')
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tfds-dir', default='data/tfds')
    parser.add_argument('--out', default='data/wiki40b/pt')
    args = parser.parse_args()
    if Path(args.out).exists():
        parser.error('Output already exists. Reuse it for training or select another --out directory.')
    configure_tensorflow_environment()
    try:
        import tensorflow_datasets as tfds
    except ImportError:
        parser.error('Install dataset support: python -m pip install -r requirements-tfds.txt')
    import tensorflow as tf
    tf.config.threading.set_intra_op_parallelism_threads(2)
    tf.config.threading.set_inter_op_parallelism_threads(1)
    download_prepared(args.tfds_dir)
    builder = tfds.builder(DATASET, data_dir=args.tfds_dir)
    def records_for_split(split):
        dataset = builder.as_dataset(split=split, shuffle_files=False,
                                     read_config=tfds.ReadConfig(try_autocache=False))
        options = tf.data.Options()
        options.threading.private_threadpool_size = 2
        dataset = dataset.with_options(options).batch(512).prefetch(2)
        for batch in tfds.as_numpy(dataset):
            for index in range(len(batch['text'])):
                yield {name: values[index] for name, values in batch.items()}

    records = {split: records_for_split(split) for split in SPLITS}
    manifest = export_records(records, args.out)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
