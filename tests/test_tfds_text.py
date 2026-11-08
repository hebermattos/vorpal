"""Offline synthetic records only: adapters, stable holdouts and source routing."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from config import load_config
from data import prepare
from prepare_tfds import ADAPTERS, export_records, format_record, make_builder
import tfds_sources

DOLLY = 'databricks_dolly:1.0.0'
SQUAD = 'squad/v2.0:3.0.0'
NI = 'natural_instructions:1.0.1'
OPUS = 'opus/en-pt_gnome:0.1.0'


def dolly(number):
    return {'instruction': f'Explain input validation principle number {number}.',
            'context': 'A defensive programming lesson.',
            'response': 'Check input types and bounds before processing the supplied value.'}


def qa(context, question, impossible=False):
    return {'context': context, 'question': question, 'is_impossible': impossible,
            'answers': {'text': [] if impossible else [b'Validate input']}}


class TFDSTextTests(unittest.TestCase):
    def setUp(self):
        self.settings = copy.deepcopy(load_config('tfds_text'))
        self.settings['runtime']['max_records_per_split'] = 10000
        self.settings['runtime']['validation_fraction'] = .1
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def export(self, identifier, records, name='cache'):
        out = self.root / name
        export_records(identifier, records, out, settings=self.settings)
        return out

    def test_all_four_adapters_preserve_text_and_unanswerable_label(self):
        samples = {DOLLY: dolly(1), SQUAD: qa('A sufficiently detailed passage about input validation.', 'What is the missing version?', True),
                   NI: {'definition': b'Explain defensive programming.', 'input': 'Validate a number.', 'output': 'Check the type and accepted range.'},
                   OPUS: {'en': b'Open the configuration file.', 'pt': 'Abra o arquivo de configuração.'}}
        for identifier, record in samples.items():
            text, group = format_record(identifier, record, self.settings['prompts'])
            self.assertTrue(text)
            self.assertEqual(len(group), 64)
        text, _ = format_record(SQUAD, samples[SQUAD], self.settings['prompts'])
        self.assertIn(self.settings['prompts']['unanswerable'], text)
        self.assertNotIn('Validate input', text)

    def test_train_only_groups_are_stable_across_order_and_answers(self):
        rows = [dolly(i) for i in range(100)]
        alternative = {**rows[0], 'response': 'Reject malformed or out-of-range input with a clear error.'}
        first = self.export(DOLLY, {'train': rows + [alternative]}, 'first')
        second = self.export(DOLLY, {'train': list(reversed(rows + [alternative]))}, 'second')
        def memberships(out):
            return {json.loads(line)['text_sha256']: split for split in ('train', 'validation')
                    for line in (out / f'{split}.jsonl').read_text().splitlines()}
        self.assertEqual(memberships(first), memberships(second))
        groups = [{json.loads(line)['group_sha256'] for line in (first / f'{split}.jsonl').read_text().splitlines()}
                  for split in ('train', 'validation')]
        self.assertTrue(groups[0] and groups[1])
        self.assertFalse(groups[0] & groups[1])
        self.assertEqual((first / 'test.bin').read_bytes(), b'')

    def test_official_squad_holdout_context_never_enters_train(self):
        held = 'A held-out passage about authentication methods and their practical limitations.'
        records = {'validation': [qa(held, 'What does the passage discuss?')],
                   'train': [qa(held, 'Which methods are mentioned?'),
                             qa('An independent training passage about validating ports and checking bounds.', 'What should be validated?')]}
        out = self.export(SQUAD, records)
        self.assertNotIn(held, (out / 'train.bin').read_text())
        self.assertIn(held, (out / 'valid.bin').read_text())
        self.assertEqual(json.loads((out / 'manifest.json').read_text())['rejected']['train']['held_out_group'], 1)

    def test_secret_invalid_rows_and_duplicates_are_filtered(self):
        rows = [dolly(i) for i in range(100)]
        rows.extend([rows[0], {**rows[1], 'response': 'api_key=abcdefghijklmno'}, {'bad': 'record'}])
        out = self.export(DOLLY, {'train': rows})
        self.assertNotIn('abcdefghijklmno', (out / 'train.bin').read_text() + (out / 'valid.bin').read_text())
        rejected = json.loads((out / 'manifest.json').read_text())['rejected']['train']
        self.assertEqual(rejected['duplicate_text'], 1)
        self.assertTrue(any('secret' in key for key in rejected))

    def test_export_is_atomic_and_existing_exports_are_preserved(self):
        with self.assertRaises(ValueError):
            self.export(DOLLY, {'train': []})
        self.assertEqual(list(self.root.iterdir()), [])
        out = self.export(DOLLY, {'train': [dolly(i) for i in range(100)]})
        original = (out / 'train.bin').read_bytes()
        with self.assertRaises(FileExistsError):
            self.export(DOLLY, {'train': []})
        self.assertEqual((out / 'train.bin').read_bytes(), original)

    def test_limit_and_preparation_manifest(self):
        self.settings['runtime']['max_records_per_split'] = 20
        out = self.export(DOLLY, {'train': [dolly(i) for i in range(100)]})
        manifest = json.loads((out / 'manifest.json').read_text())
        self.assertEqual(sum(item['documents'] for item in manifest['splits'].values()), 20)
        self.assertEqual(manifest['preparation'], self.settings)
        self.assertTrue((out / 'LICENSE.txt').exists())

    def test_cached_generic_sources_integrate_without_download_or_training(self):
        rows = [dolly(i) for i in range(100)]
        rows[0]['response'] += ' A literal marker in code can be _START_CODE_.'
        out = self.export(DOLLY, {'train': rows})
        source = {'dataset': DOLLY, 'out': str(out)}
        def settings(name):
            return self.settings if name == 'tfds_text' else {'download_dir': 'unused'}
        corpus = self.root / 'corpus'
        corpus.mkdir()
        with patch.object(tfds_sources, 'load_config', side_effect=settings), patch.object(tfds_sources.subprocess, 'run') as download, contextlib.redirect_stdout(io.StringIO()):
            documents = list(tfds_sources.documents([source]))
            prepare(corpus, self.root / 'data', external_documents=documents)
        self.assertEqual(len(documents), 100)
        download.assert_not_called()
        manifest = json.loads((self.root / 'data/manifest.json').read_text())
        self.assertTrue(manifest['train_document_hashes'] and manifest['valid_document_hashes'])
        self.assertFalse(set(manifest['train_document_hashes']) & set(manifest['valid_document_hashes']))

    def test_discovery_accepts_new_registered_sources(self):
        corpus = self.root / 'corpus'
        corpus.mkdir()
        (corpus / 'tfds').write_text('\n'.join(ADAPTERS))
        self.assertEqual({s['dataset'] for s in tfds_sources.discover_sources(corpus)}, set(ADAPTERS))

    def test_generic_preparation_routes_to_adapter_without_real_subprocess(self):
        original = self.export(DOLLY, {'train': [dolly(i) for i in range(100)]})
        target = self.root / 'prepared'
        def fake_prepare(command, check):
            import shutil
            self.assertEqual(Path(command[1]).name, 'prepare_tfds.py')
            self.assertEqual(command[command.index('--dataset') + 1], DOLLY)
            self.assertTrue(check)
            shutil.copytree(original, target)
        def settings(name):
            return self.settings if name == 'tfds_text' else {'download_dir': 'unused'}
        with patch.object(tfds_sources, 'load_config', side_effect=settings), patch.object(tfds_sources.subprocess, 'run', side_effect=fake_prepare), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tfds_sources.ensure_dataset({'dataset': DOLLY, 'out': str(target)}), target)

    def test_changed_export_settings_do_not_silently_reuse_cache(self):
        out = self.export(DOLLY, {'train': [dolly(i) for i in range(100)]})
        changed = copy.deepcopy(self.settings)
        changed['runtime']['max_records_per_split'] += 1
        def settings(name):
            return changed if name == 'tfds_text' else {'download_dir': 'unused'}
        with patch.object(tfds_sources, 'load_config', side_effect=settings), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'settings changed'):
                tfds_sources.ensure_dataset({'dataset': DOLLY, 'out': str(out)})

    def test_holdout_priority_applies_across_selected_source_caches(self):
        common = qa('A shared context about checking configuration and applying defensive validation.', 'What should be checked?')
        other = qa('An independent context about safe error handling and structured logging.', 'What is described?')
        first = self.export(SQUAD, {'train': [common]}, 'first')
        second = self.export(SQUAD, {'validation': [common], 'train': [other]}, 'second')
        sources = [{'dataset': SQUAD, 'out': str(path)} for path in (first, second)]
        def settings(name):
            return self.settings if name == 'tfds_text' else {'download_dir': 'unused'}
        corpus = self.root / 'corpus'
        corpus.mkdir()
        with patch.object(tfds_sources, 'load_config', side_effect=settings), contextlib.redirect_stdout(io.StringIO()):
            prepare(corpus, self.root / 'data', external_documents=tfds_sources.documents(sources))
        formatted, _ = format_record(SQUAD, common, self.settings['prompts'])
        self.assertNotIn(formatted, (self.root / 'data/train.bin').read_text())
        self.assertIn(formatted, (self.root / 'data/valid.bin').read_text())

    def test_builder_routes_opus_custom_english_portuguese_config(self):
        from unittest.mock import Mock
        tfds = Mock()
        make_builder(tfds, OPUS, 'unused')
        config = tfds.translate.opus.OpusConfig.call_args.kwargs
        self.assertEqual(config['language_pair'], ('en', 'pt'))
        self.assertEqual(config['subsets'], ['GNOME'])
        make_builder(tfds, DOLLY, 'unused')
        tfds.builder.assert_called_with(DOLLY, data_dir='unused')
