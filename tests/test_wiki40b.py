"""Official split isolation and normalization for the optional TFDS importer."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from prepare_wiki40b import clean_text, export_records
from mix_wiki40b import mix_training


def record(identifier, text):
    return {'wikidata_id': identifier.encode(), 'version_id': b'version', 'text': text.encode()}


class Wiki40BTests(unittest.TestCase):
    def test_markers_and_line_wrapping_are_cleaned_with_accents_preserved(self):
        text = b'_START_ARTICLE_\nT\xc3\xadtulo\n_START_SECTION_\nSe\xc3\xa7\xc3\xa3o\n_START_PARAGRAPH_\nUma  frase._NEWLINE_Outra frase.'
        self.assertEqual(clean_text(text), 'Título\n\nSeção\n\nUma frase.\nOutra frase.')
        with self.assertRaises(ValueError):
            clean_text('_START_UNKNOWN_ exemplo')

    def test_holdout_ids_and_normalized_text_never_enter_training(self):
        records = {
            'test': [record('Q1', 'Texto reservado ao teste, com informação para avaliar o modelo.')],
            'validation': [record('Q1', 'Outro texto da mesma entidade, que deve ser excluído.'),
                           record('Q2', 'Texto reservado à validação, com conteúdo separado do treino.')],
            'train': [record('Q1', 'Uma versão diferente da entidade de teste não pode entrar no treino.'),
                      record('Q3', '_START_ARTICLE_\nTexto reservado à validação, com conteúdo separado do treino.'),
                      record('Q4', 'Exemplo com credencial que deve ser rejeitada: api_key=abcdefghijklmno'),
                      record('Q5', '_START_UNKNOWN_ Texto com um marcador que não sabemos interpretar.'),
                      record('Q6', 'Um texto independente e suficientemente longo para treinar o modelo em português.')],
        }
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / 'prepared'
            manifest = export_records(records, out)
            train = [json.loads(s) for s in (out / 'train.jsonl').read_text().splitlines()]
            self.assertEqual([item['wikidata_id'] for item in train], ['Q6'])
            rejected = manifest['splits']['train']['rejected']
            self.assertEqual(rejected, {'duplicate_entity': 1, 'duplicate_text': 1, 'secret_content': 1, 'invalid_record': 1})
            for split, name in [('train', 'train.bin'), ('validation', 'valid.bin'), ('test', 'test.bin')]:
                raw = (out / name).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), manifest['splits'][split]['sha256'])
                for entry in map(json.loads, (out / f'{split}.jsonl').read_text().splitlines()):
                    payload = raw[entry['offset']:entry['offset'] + entry['bytes']]
                    self.assertEqual(hashlib.sha256(payload.decode().rstrip('\n').encode()).hexdigest(), entry['text_sha256'])
            before = (out / 'train.bin').read_bytes()
            with self.assertRaises(FileExistsError):
                export_records(records, out)
            self.assertEqual((out / 'train.bin').read_bytes(), before)

    def test_failed_export_does_not_publish_partial_dataset(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / 'prepared'
            with self.assertRaises(ValueError):
                export_records({'test': [], 'validation': [], 'train': []}, out)
            self.assertFalse(out.exists())
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_mixture_replays_only_local_train_and_official_wiki_train(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wiki, corpus = root / 'wiki', root / 'local'
            corpus.mkdir()
            wiki_text = 'Texto de treino da enciclopédia sobre uma atividade cultural e seus participantes.'
            export_records({'train': [record('Q1', wiki_text)],
                            'validation': [record('Q2', 'Este artigo está reservado para a validação oficial do conjunto de dados.')],
                            'test': [record('Q3', 'Este outro artigo está reservado para o teste oficial e não será treinado.')]}, wiki)
            teaching = 'Pergunta: Como organizar tarefas?\nResposta: Faça uma lista e escolha as prioridades.\n'
            held = 'Pergunta: Como proteger uma conta?\nResposta: Ative a segunda verificação.\n'
            (corpus / 'qa_ok.txt').write_text(teaching)
            (corpus / 'qa_held.txt').write_text(held)
            state = root / 'split_state.json'
            state.write_text(json.dumps({'train_document_hashes': [hashlib.sha256(teaching.encode()).hexdigest()],
                                         'valid_document_hashes': [hashlib.sha256(held.encode()).hexdigest()]}))
            out = root / 'mixed'
            manifest = mix_training(wiki, corpus, state, out, articles=1)
            payload = (out / 'train.bin').read_text()
            self.assertIn(wiki_text, payload)
            self.assertIn(teaching.strip(), payload)
            self.assertNotIn(held.strip(), payload)
            self.assertIn(held.strip(), (out / 'valid_local.bin').read_text())
            self.assertNotIn('reservado', payload)
            self.assertEqual(len(manifest['local_documents']), 1)
            self.assertEqual(hashlib.sha256((out / 'train.bin').read_bytes()).hexdigest(), manifest['sha256'])
            with self.assertRaises(FileExistsError):
                mix_training(wiki, corpus, state, out)
            with self.assertRaises(ValueError):
                mix_training(wiki, corpus, state, root / 'bad', articles=0)
