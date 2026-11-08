"""Regression tests for profile overlap and prepared dataset safety."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from data import prepare, register_prepared_training
from data_quality import audit
import train


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


class SharedSplitTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.root = Path(temporary)
        self.corpus, self.data = self.root / 'corpus', self.root / 'data'
        self.security = self.corpus / 'security'
        self.security.mkdir(parents=True)
        self.profile = self.data / 'security'
        self.docs = sorted(
            (f'Synthetic document {i} for local shared split regression testing.\n' for i in range(3)),
            key=digest,
        )
        (self.corpus / 'extra.txt').write_text(self.docs[0])
        (self.security / 'overlap.txt').write_text(self.docs[1])
        (self.security / 'second.txt').write_text(self.docs[2])

    def test_complete_and_security_keep_assignments_when_prepared_in_either_order(self):
        for first_is_complete in (False, True):
            with self.subTest(first_is_complete=first_is_complete):
                split_root = self.root / str(first_is_complete)
                profiles = [(self.security, split_root / 'security'), (self.corpus, split_root)]
                ordered = profiles[::-1] if first_is_complete else profiles
                for corpus, output in ordered:
                    prepare(corpus, output, split_root=split_root)
                # A later replay prepares the full subject again, including consumed documents.
                results = [prepare(corpus, output, split_root=split_root) for corpus, output in profiles]
                training = {value for result in results for value in result['train_document_hashes']}
                validation = {value for result in results for value in result['valid_document_hashes']}
                self.assertTrue(training)
                self.assertTrue(validation)
                self.assertFalse(training & validation)
                self.assertIn(digest(self.docs[1]), training if first_is_complete else validation)

    def test_legacy_conflict_is_reported_and_reset_keeps_checkpoint(self):
        prepare(self.security, self.profile)
        prepare(self.corpus, self.data)
        report = audit(self.security, self.profile, split_root=self.data)
        self.assertFalse(report['integrity_passed'])
        self.assertIn(digest(self.docs[1]), report['findings'][0])
        checkpoint = self.root / 'latest.keras'
        metadata = self.root / 'latest.json'
        checkpoint.write_bytes(b'checkpoint sentinel')
        metadata.write_bytes(b'metadata sentinel')
        args = SimpleNamespace(corpus=str(self.security), out=str(self.profile),
                               split_root=self.data, data=None, reset=True)
        with patch.object(train, 'CHECKPOINT', checkpoint), patch.object(train, 'METADATA', metadata), \
                patch.object(train, 'import_directory'), self.assertRaisesRegex(ValueError, digest(self.docs[1])):
            train.prepare_training_corpus(args)
        self.assertEqual(checkpoint.read_bytes(), b'checkpoint sentinel')
        self.assertEqual(metadata.read_bytes(), b'metadata sentinel')
        self.assertFalse((self.data / 'shared_split_state.json').exists())

    def test_official_test_stays_held_out_in_a_later_profile(self):
        text = 'Official test fixture with content also copied into another corpus.'
        prepare(self.security, self.profile, split_root=self.data,
                external_documents=[('official-test', text, 'test')])
        (self.corpus / 'local_copy.txt').write_text(text + '\n')
        result = prepare(self.corpus, self.data, split_root=self.data)
        self.assertNotIn('local_copy.txt', result['train_documents'] + result['valid_documents'])
        registry = json.loads((self.data / 'shared_split_state.json').read_text())
        self.assertIn(digest(text), registry['test_document_hashes'])

    def test_managed_prepared_binary_rejects_held_out_paths_and_same_size_tampering(self):
        prepare(self.security, self.profile, split_root=self.data)
        path = self.profile / 'train.bin'
        register_prepared_training(path, self.data)
        args = SimpleNamespace(prepared=True, data=str(self.profile / 'valid.bin'),
                               batch=1, steps=1, lr=0.001, split_root=self.data)
        with patch.object(train, 'configure_cpu') as configure_cpu, \
                self.assertRaisesRegex(ValueError, 'held-out binary'):
            train.run(args)
        configure_cpu.assert_not_called()
        path.write_bytes(b'x' * path.stat().st_size)
        with self.assertRaisesRegex(ValueError, 'differs from its manifest'):
            register_prepared_training(path, self.data)
        (self.profile / 'manifest.json').unlink()
        with self.assertRaisesRegex(ValueError, 'manifest is missing'):
            register_prepared_training(path, self.data)

    def test_existing_split_propagates_to_trailing_whitespace_variant(self):
        result = prepare(self.security, self.profile, split_root=self.data)
        self.assertIn(digest(self.docs[1]), result['valid_document_hashes'])
        (self.security / 'overlap.txt').write_text(self.docs[1] + '\n')
        result = prepare(self.corpus, self.data, split_root=self.data)
        self.assertIn(digest(self.docs[1] + '\n'), result['valid_document_hashes'])
