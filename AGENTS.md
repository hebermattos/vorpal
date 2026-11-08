# AGENTS.md

This repository contains the Vorpal project, an educational prototype of an autoregressive model built with TensorFlow/Keras for programming data and defensive cybersecurity.

## Project objectives and requirements

- The project aims to identify vulnerabilities associated with a specific IP address on an authorized local network.
- Use the trained model to guide vulnerability analysis as effectively as possible, while retaining command validation and human review.
- Organize training by subject: Python, security, languages, and tools. Treat these as training categories; the currently available profiles are documented below.
- Keep all configuration, prompts, and restriction rules in files.
- Always use English for project content and communication.
- Provide a single training command that imports URLs and files from the selected corpus directory and incorporates their data into the model.
- Support training from scratch, incremental training using only new data, and retraining with previously used data.
- Save logs for both training runs and vulnerability analyses.

## General rules

- Work with Python 3.11, preferably on Linux/WSL.
- Keep focus on compatibility with TensorFlow/Keras on CPU.
- Avoid broad refactors unless necessary; prefer small, targeted changes.
- Preserve compatibility with existing import, training, evaluation, and generation flows.
- Do not introduce new dependencies unless there is a clear need.
- Do not run suspicious or unauthorized content downloaded from external sources.

## Important project structure

- `README.md`: project overview and main commands.
- `config/`: validated JSON defaults, including the shared profile registry in `config/web.json`.
- `src/web.py` and `web/`: local dashboard and background task runner.
- `src/security_tools.py`: model-proposed and validated Nmap/Nuclei commands.
- `src/benchmark.py` and `src/code_quality.py`: syntax and limited AST behavior checks without executing generated Python.
- `src/import_dataset.py`: data import and validation.
- `src/scraping.py` and `config/scraping.json`: bounded same-host crawling from corpus `scraping` source lists, with robots checks and incremental link caching.
- `src/tfds_sources.py` and `config/tfds.json`: discovery of corpus `tfds` source lists and verified, reviewed TensorFlow Datasets adapters (Wiki40B Portuguese, Dolly, SQuAD v2, Natural Instructions and OPUS English–Portuguese GNOME).
- `src/ingest_url.py`: backward-compatible adapter to the canonical URL importer.
- `src/content_safety.py`: shared secret-content filter.
- `src/data.py`: corpus preparation and train/validation split.
- `src/model.py`: causal Transformer model.
- `src/train.py`: training and checkpoints.
- `src/evaluate.py`: loss and byte-level perplexity.
- `src/generate.py`: local text generation.
- `tests/`: automated tests.
- `corpus/security/`: security sources and its `url`, `pdfs/` and `imported/` inputs.
- `corpus/python/`: Python sources, examples and local PDF books.
- `corpus/tools/nmap/` and `corpus/tools/nuclei/`: tool-specific sources and planning examples.
- `corpus/imported/`: legacy global cache retained for the complete profile.

## Expected commands

### Tests

```bash
python -m unittest discover -s tests -v
```

### Typical workflow

```bash
python src/train.py --steps 500 --batch 8 --threads 4
python src/evaluate.py --samples 50 --threads 4
python src/generate.py --prompt "def validate_port(" --max-new 160 --threads 4
```

`src/train.py` imports only new sources under the selected corpus, trains only unconsumed document hashes by default, and automatically resumes its checkpoint. Use `--profile security`, `python`, `nmap`, `nuclei`, or `complete` to select the shared registry in `config/web.json`. Each profile has its own corpus, prepared dataset and history, while all phases use the same configured checkpoint. Without a profile, the defaults in `config/train.json` still apply; explicit `--corpus`/`--out` override profile paths.

Each `--steps` value adds that many steps when eligible documents exist. `--replay` includes previously used documents and requires a checkpoint plus metadata. `--reset` rebuilds the shared latest model from step zero and forces reimport of the selected corpus; it clears only that output's consumed-document state, preserving split assignments and other profiles' histories. Run `src/data.py` separately only to prepare existing text; it does not fetch URLs or extract PDFs. For subject evaluation, use `src/evaluate.py --data <profile-output>/valid.bin`.

For example, continue the Nmap phase with `python src/train.py --profile nmap --replay --steps 500 --threads 4`. Keep persisted split assignments and document hashes compatible when moving or deduplicating sources. New checkpoints preserve NumPy window sampling state, but full TensorFlow execution is not guaranteed to resume bit-for-bit.

`src/train.py` also discovers UTF-8 files named `tfds` under the selected corpus, with one supported versioned dataset identifier per line. It prepares missing datasets automatically and reuses verified caches. `corpus/pt_br/tfds` enables `wiki40b/pt:1.3.0`; install `requirements-tfds.txt` once before using it. Additional reviewed text adapters and disabled source examples are documented in `docs/TFDS_DATASETS.md`; templates, export limits and local holdout fraction are in `config/tfds_text.json`. Official validation/test assignments must never become training data. Normal, replay and reset modes use the existing consumed-document tracking; reset reuses the source cache. `--prepared` bypasses source discovery, and `src/data.py` alone does not download datasets.

Files named `scraping` list public HTTP(S) starting URLs, one per line. The normal corpus importer follows links only within each seed's origin and containing directory (including subdirectories); redirects and cached links obey the same scope. The origin's robots.txt is a separate metadata request. The importer applies configured page/depth/byte limits, robots rules and request pacing. Successful pages and their discovered links are reused; failed pages retry. Preserve `scraping_state.json` as import metadata and exclude it from training. `--reset` refreshes scraping sources as well as regular imports.

## Code guidelines

- Keep the API and script arguments compatible with the existing project interfaces.
- Preserve compatibility for `data/*.bin`, checkpoints, and imports.
- If you change the data pipeline, validate the impact on tests and `README.md`.
- When handling external input, keep defensive validation and explicit error handling.
- Do not assume imported data is trustworthy; the filters are heuristic.
- Keep error messages useful and clear.

## Security and ethics

- The project is experimental and should not be treated as a reliable vulnerability detector.
- Generated code and input analysis should be reviewed by humans before execution in sensitive environments.

## Before finishing changes

- Run the relevant tests.
- Verify the change does not break import, training, or generation workflows.
- Update documentation when the interface or behavior of the project changes.
