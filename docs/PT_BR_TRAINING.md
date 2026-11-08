# PT-BR language training experiment

The corpus under `corpus/pt_br/` combines the original 134 locally authored
teaching documents with 209 non-overlapping chapter groups from Brazilian fiction.
The additional texts total 1,158,171 UTF-8 bytes after normalization.

| Work | Author | Source | Chapter groups |
| --- | --- | --- | --- |
| Dom Casmurro | Machado de Assis | https://www.gutenberg.org/ebooks/55752 | 96 |
| Memorias Posthumas de Braz Cubas | Machado de Assis | https://www.gutenberg.org/ebooks/54829 | 98 |
| Triste Fim de Polycarpo Quaresma | Lima Barreto | https://www.gutenberg.org/ebooks/67535 | 15 |

The source pages identify the editions as public domain in the USA. These are
historical works by authors who died in 1908 and 1922, respectively. Original
Gutenberg wrappers and publication front matter were excluded from training.
Wrapped lines were joined within paragraphs, underscore emphasis was removed,
and adjacent whole chapters were grouped to at least 2,048 bytes. A final short
group was merged into its predecessor. Historical spelling was retained.
`corpus/pt_br/literatura/SOURCES.tsv` records the source URLs and raw-file hashes;
`SHA256SUMS` records the resulting document hashes. Full source snapshots,
transformation manifests, backup paths, evaluation outputs and generation samples
are stored locally under `reports/ptbr-expansion/` and `logs/backups/`, outside Git.

These books teach narrative language, not current spelling rules or security
expertise. They contain period-specific views and vocabulary. Validation is by
persisted document hash; chapter groups from the same book may occur on both
sides of the split, so this is not an evaluation on independent books or authors.
The original validation set is also checked to observe changes on the earlier
teaching examples. Its overlap in topics with training limits its independence.

An initial sample of translated conversations from Tucano-SFT was rejected after
manual spot checks found malformed Portuguese. None of those sampled files were
retained in the training corpus.

## Run

These local-only results predate the addition of `corpus/pt_br/tfds`. Current
corpus training commands also include Wiki40B when that source list is present;
see [the automatic dataset workflow](WIKI40B_TRAINING.md#automatic-corpus-workflow).

The experiment preserves the 512-byte architecture and resumes the checkpoint.
It does not reset the model or alter the persisted split history. Each cycle adds
500 steps:

```bash
python src/train.py --corpus corpus/pt_br --out data/pt_br --replay --steps 500 --batch 4 --threads 4
python src/evaluate.py --data data/pt_br/valid.bin --samples 50 --threads 4
python src/generate.py --question "Como proteger minhas contas contra phishing?" --max-new 120 --temperature 0.3 --threads 4
python src/generate.py --prompt "Pergunta: Como organizar as tarefas do dia?
Resposta:" --max-new 120 --temperature 0.3 --threads 4
```

The training runner imports the new text and prepares the combined corpus. It
also includes previously consumed documents because `--replay` is supplied.
Loss values before and after this expansion are comparable only when measured
on the same held-out bytes with the same sampling settings. Lower byte loss does
not establish factual correctness, instruction following or usable Portuguese.

## Results — 2026-11-07

Evaluation uses the same 50 sampled windows per held-out corpus before and after
each cycle. The two corpora are reported separately.

| Accumulated step | Expanded loss | Expanded byte perplexity | Original loss | Original byte perplexity |
| --- | --- | --- | --- | --- |
| 500 | 2.6277 | 13.84 | 2.2918 | 9.89 |
| 1000 | 2.3556 | 10.54 | 2.3756 | 10.76 |
| 1500 | 2.3367 | 10.35 | 2.3678 | 10.67 |
| 2000 | 2.3169 | 10.14 | 2.3567 | 10.56 |

The expanded-corpus loss improved from 2.6277 to 2.3169. The original-corpus
loss worsened from 2.2918 to 2.3567, so improvement did not carry over uniformly.
Both fixed generation prompts still produced incoherent text after all three
cycles. A security-generation check at temperature 0.8 was also incoherent.
This experiment did not produce usable Portuguese or a reliable security assistant.

The current model has 434,272 parameters and a 512-byte context. These results
do not isolate whether data volume, historical spelling, capacity, sampling or
training duration is the principal limitation. Increasing the context alone
does not solve language learning.

No reset or checkpoint-format change was performed. The final latest checkpoint
is at step 2,000. The pre-experiment checkpoint and dataset are retained under
`logs/backups/before-ptbr-expansion-20261107-183442/`. An intermediate snapshot
at step 1,500 is also retained locally. Complete logs and generated samples are
in `reports/ptbr-expansion/`.
