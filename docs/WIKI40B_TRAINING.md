# One Vorpal model, with Wiki40B Portuguese data

Wiki40B is a dataset, not another model. This workflow resumes the existing
Vorpal TensorFlow/Keras checkpoint. It does not install or use GPT-2. `latest.keras`
and `best.keras` remain saved versions of the same Vorpal architecture.

## Automatic corpus workflow

The file `corpus/pt_br/tfds` lists `wiki40b/pt:1.3.0`, one identifier per line,
with blank lines and `#` comments allowed. Install support once, then train normally:

```bash
python -m pip install -r requirements-tfds.txt
python src/train.py --corpus corpus/pt_br --out data/pt_br --steps 500 --batch 4 --threads 4
```

Training discovers `tfds` files recursively in the selected corpus, downloads and
exports missing data, verifies cached binaries/index records, and combines official
training/validation documents with local text. Official test text is excluded.
The complete profile includes this source too. Normal runs skip consumed documents;
`--replay` includes them again, and `--reset` resets the shared model while reusing
the verified cache. Persisted local split conflicts are rejected rather than moving
held-out text into training. Additional reviewed adapters are described in [the dataset guide](TFDS_DATASETS.md);
paths and source registration are in `config/tfds.json`.

The standalone and prepared-binary workflows below remain available. They bypass
the automatic corpus integration and are useful for isolated or mixed experiments.

## Prepare official data separately

```bash
python -m pip install -r requirements-tfds.txt
python src/prepare_wiki40b.py --tfds-dir data/tfds --out data/wiki40b/pt
```

The source is **wiki40b/pt:1.3.0**, from the
[official TensorFlow Datasets catalog](https://www.tensorflow.org/datasets/catalog/wiki40b).
It includes Portuguese varieties and is not exclusively Brazilian Portuguese.
The prepared TFRecords are downloaded from Google's public `tfds-data` storage
bucket over HTTPS. Object generations, MD5 checksums, sizes and SHA-256 hashes are
recorded in the local TFDS cache. This avoids Apache Beam regeneration when the
installed TFDS version disables its legacy GCS download helper.

The exporter uses the official TFDS reader with batched iteration. It normalizes
Unicode to NFC, removes `_START_ARTICLE_`, `_START_SECTION_`, `_START_PARAGRAPH_`
and `_NEWLINE_` markers, and preserves headings and accents. Malformed, very short
and secret-bearing content is excluded with the existing heuristic filter.
The filter is not a guarantee that the corpus is safe, accurate or unbiased.

Official test and validation entity IDs and normalized text hashes take priority
over train. Duplicate records never migrate from a held-out split into training.
Files are published only after all splits finish. Existing output directories are
not overwritten. The output is outside `corpus/`, preventing the regular recursive
importer from inadvertently resplitting held-out documents.

Outputs:

- `train.bin`, `valid.bin`, `test.bin`: the existing Vorpal UTF-8 byte format.
- `train.jsonl`, `validation.jsonl`, `test.jsonl`: source titles, article URLs,
  Wikidata IDs, version fields, byte offsets and normalized text hashes.
- `manifest.json`: source, transformations, accepted/rejected counts and hashes.
- `LICENSE.txt`: attribution information and the source's CC BY-SA 3.0 license link.

Keep attribution and share-alike requirements with redistributed text; see the
[CC BY-SA 3.0 terms](https://creativecommons.org/licenses/by-sa/3.0/).
Raw downloads, indexes and binaries are ignored under `data/`.

## Continue the same model

Back up `checkpoints/` before an experiment. For the language phase:

```bash
python src/train.py --prepared --resume --data data/wiki40b/pt/train.bin --validation-data data/wiki40b/pt/valid.bin --steps 500 --batch 4 --threads 4
```

`--prepared` bypasses source import, resplitting and consumed-document history
updates. It requires `--data` and rejects `--reset` and `--replay`. Recognized
prepared datasets are checked against the shared split registry before use.
Vorpal verifies recorded checksums when available. Raw byte files without
document metadata remain supported, and their document split must be verified
by the operator. Pass validation data explicitly. Prepared mode does not silently
use another corpus's `valid.bin`. `--resume` requires the existing checkpoint and
matching architecture. These commands retain the current 512-byte context.

For the mixed phase:

```bash
python src/mix_wiki40b.py --articles 1000
python src/train.py --prepared --resume --data data/wiki40b/pt-mixed/train.bin --validation-data data/wiki40b/pt-mixed/valid_local.bin --steps 500 --batch 4 --threads 4
```

The mixture contains the first 1,000 accepted official training articles and the
local teaching documents already assigned to training in `data/pt_br/split_state.json`.
It replays short local examples to approximately balance byte volume with Wiki40B;
this is repetition of existing data, not new independent examples. Historical
novels are not added to this mixture. Local validation documents are saved
separately as `valid_local.bin`. Wiki40B validation/test text is excluded from
both mixture training sources. Source hashes and composition are recorded in its
manifest. Existing mixture output is not overwritten.

## Evaluate

```bash
python src/evaluate.py --data data/wiki40b/pt/valid.bin --samples 50 --threads 4
python src/evaluate.py --data data/wiki40b/pt-mixed/valid_local.bin --samples 50 --threads 4
python src/generate.py --question "Como proteger minhas contas contra phishing?" --max-new 120 --temperature 0.3 --threads 4
```

Compare the same held-out bytes and sampling settings before and after each phase.
The experiment does not evaluate `test.bin` during training/tuning. Local teaching
examples are related in subject and wording, so their validation is not an
independent security benchmark. A short cycle samples only a small fraction of
the full Wiki40B corpus. More data and lower byte loss do not guarantee fluency,
instruction following, factual correctness or security expertise.
