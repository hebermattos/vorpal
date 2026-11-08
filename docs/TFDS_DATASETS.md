# Reviewed TFDS text adapters

These adapters extend the existing `tfds` source-list workflow. Adding code or
uncommenting a source does not itself download data or train the model. Running
normal corpus training after activation prepares missing datasets automatically.

## Activate a source

Install optional support once with `python -m pip install -r requirements-tfds.txt`.
Uncomment only the desired identifiers in `corpus/instructions/tfds`:

```text
databricks_dolly:1.0.0
squad/v2.0:3.0.0
natural_instructions:1.0.1
```

When ready, train that corpus with:

```bash
python src/train.py --corpus corpus/instructions --out data/instructions --steps 500 --threads 4
```

For English–Portuguese translation examples, uncomment this line in
`corpus/pt_br/tfds`:

```text
opus/en-pt_gnome:0.1.0
```

That custom adapter uses OPUS's `GNOME` subset and the `en`/`pt` language pair.
It teaches localization examples; it does not provide Brazilian-Portuguese-only
text or security expertise. The existing Wiki40B entry remains independent.

The new example lines ship commented. The complete profile discovers activated
sources anywhere under `corpus/`; the Nmap profile remains scoped to its own corpus.
All phases update the configured shared checkpoint.

## Formats and split policy

| Source | Exported text | Split policy |
| --- | --- | --- |
| [Dolly](https://www.tensorflow.org/datasets/catalog/databricks_dolly) | Instruction, context, response | Stable hash of instruction/context group |
| [SQuAD v2](https://www.tensorflow.org/datasets/catalog/squad) | Context, question, answer or explicit unavailable-answer label | Official splits; held-out contexts excluded from train |
| [Natural Instructions](https://www.tensorflow.org/datasets/catalog/natural_instructions) | Definition, input, output | Stable hash of definition/input group |
| [OPUS](https://www.tensorflow.org/datasets/catalog/opus) | English and Portuguese text | Stable hash of English source text |

Train-only datasets receive a deterministic local validation split. Their official
source split is recorded in each index entry. These splits do not establish
independent task, topic or source generalization. Identical formatted documents
are deduplicated, with held-out documents processed before training across all
selected datasets. Dataset content is not executed.

Templates and the unanswerable label are in `config/tfds_text.json`. The defaults
export at most 10,000 source records per official split, reject short/invalid/secret-
bearing text, and reserve approximately 10% of prompt groups for validation when
no official held-out split exists. Export limits apply before filtering; actual
counts may be lower. Small samples can have no usable validation documents.
The full upstream dataset may still need downloading/preparing, especially for
Natural Instructions; this is not a capped network download.

Exports live under the paths registered in `config/tfds.json`, outside the corpus.
They use the existing byte format and per-split JSONL offsets/hashes. Preparation
is atomic, verifies dataset identity and cached binary/index integrity, and refuses
to overwrite existing exports. Changed templates or limits require selecting a
new export path; keep existing histories for reproducibility. Official test
payloads never enter training. Existing Wiki40B caches remain compatible.

The 512-byte model context is unchanged. Long instruction/context/answer examples
may not fit together in one sampled training window. These adapters add data
formats, not guaranteed instruction-following ability.

Dolly specifies CC BY-SA 3.0. Natural Instructions aggregates sources with their
own licenses; OPUS subset material also requires source-specific review. Exported
manifests and `LICENSE.txt` retain source/attribution notes, not a universal license
grant. Consult the linked upstream catalogs and source terms before redistribution.
