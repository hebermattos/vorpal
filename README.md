# Vorpal — Local AI with TensorFlow/Keras

Vorpal is an educational autoregressive model project **trained from scratch**, experimentally specialized in programming data and defensive cybersecurity. The training and inference backend is **TensorFlow/Keras on CPU**, without PyTorch or pre-trained models.

> Research prototype: default context window of only 128 **bytes** and a small parameter count. It is not a reliable vulnerability detector and does not replace security review.

## Environment

- Python 3.11 recommended (Linux/Ubuntu/WSL).
- CPU only; about 8 GB of RAM is recommended for the default model.
- TensorFlow 2.16+ and Keras 3 via `tf.keras` (see `requirements.txt`).
- A clean install is recommended during migration.

```bash
git clone https://github.com/hebermattos/vorpal.git
cd vorpal
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt
```

## Dataset import — preserved

The importer remains **independent of the training backend** and accepts source code (.py, .js, .ts, .java, .kt, .go, .rs, .c, .cpp, .sh, .nse), documentation (.pdf, .docx, .md, .rst, .html, .txt), data (.json, .jsonl, .csv, .xml, .toml, .yaml), and security files (.sarif, .nessus, .har), including ZIP/TAR.GZ projects.

```bash
python src/import_dataset.py --file ./document.pdf
python src/import_dataset.py --directory ./my-data
python src/import_dataset.py --url https://example.org/documentation.html
```

### `url` files inside folders

Inside `./my-data` or any subfolder, place a file named exactly **`url`** (no extension), UTF-8 encoded, with one public HTTP(S) URL per line:

```text
# authorized documentation
https://docs.python.org/3/library/secrets.html
https://example.org/guide.pdf
```

`--directory` imports local documents, compressed archives, and URLs from all subfolders. Duplicate URLs are deduplicated within the same run. Failures are recorded in `corpus/imported/import_report.json` without stopping other downloads. The `url` file is not turned into training text. The importer prints each file and URL as it is processed.

**Security:** validate licensing and authorization, review personal data and secrets; the filters are heuristic. There is no per-file, download, URL-list, or PDF-page size limit, so only use trusted sources and ensure sufficient memory and disk space. Archives still have member-count and compression-ratio safeguards. Blocking private addresses by DNS verification does not eliminate DNS rebinding risk; use network isolation with untrusted sources. Downloaded content is never executed and OCR is not performed.

## Prepare, train, evaluate, and generate

```bash
python src/train.py --steps 500 --batch 8 --threads 4
python src/evaluate.py --samples 50 --threads 4
python src/generate.py --prompt "def validate_port(" --max-new 160 --threads 4
```

`src/train.py` imports new local supported files and public URLs listed in `corpus/**/url`, prints each source to the console, prepares only documents not previously used, and incrementally resumes the existing checkpoint. Import failures are recorded in `corpus/imported/import_report.json`; successful extracted text is included in the generated dataset. Each run's `--steps` value is the number of additional optimization steps. Use `--reset` to force reimport all sources, clear training history and checkpoints, and train the full corpus from step zero. To prepare data without starting training, run `python src/data.py --corpus corpus --out data`. Outputs `data/train.bin` and `data/valid.bin` keep the **same byte format as before**. Training uses byte-level tokenization (256 possible byte values), causal attention, AdamW, and gradient clipping.

Each training command saves console output and Python errors to a timestamped file under `logs/` while continuing to display them in the terminal. Use `--log-dir PATH` to choose another log folder.

Successful imports are recorded in `corpus/imported/import_state.json`; training-consumed document hashes are stored in `data/training_state.json`. Later runs skip the same URLs, unchanged local files, and documents already consumed by training; changed files are imported and trained as new content. Failed imports are retried on the next run.

Default architecture: 3 Transformer blocks, width 96, four attention heads, context of 256 bytes. The longer context lets the model use more surrounding code, while increasing attention compute and memory. Arguments `--dim`, `--heads`, `--layers`, `--context`, `--batch`, and `--steps` allow reducing CPU/RAM usage. Existing checkpoints with the old context are incompatible; run once with `--reset` to start the new architecture from scratch.

The checkpoint is stored in `checkpoints/latest.keras` (model and optimizer state) and `checkpoints/latest.json` (configuration and cumulative step). It resumes automatically when present. `--resume` remains available to explicitly require a checkpoint.

```bash
python src/train.py --steps 1000 --batch 8 --threads 4
```

When resuming, the architecture configuration must be identical. The checkpoint restores optimizer state, but the random sample generator is not restored bit-for-bit. To discard history and train all sources from scratch, run `python src/train.py --reset --steps 500 --batch 8 --threads 4`.

## Local network report

For explicit local testing on a private IPv4 address, the project includes a bounded report generator that checks common ports and writes a JSON file. It does not execute payloads or rely on shell commands. It only accepts private or link-local IPv4 addresses and requires the `--authorized` flag.

```bash
./vorpal.py 192.168.1.25 --authorized --ports 22,80,443,3306,5432 --output reports/local_scan.json
./vorpal.py 192.168.1.25 --authorized --checkpoint checkpoints/latest.keras --output reports/local_scan.json
```

When a trained checkpoint is available, the scanner uses the model's learned language patterns to compare risky and safe service descriptions before classifying exposure. If no checkpoint is present, it falls back to conservative port-based heuristics and logs that limitation clearly.

The console prints the full flow step by step:

1. validate the target IP
2. enumerate the selected ports
3. test each port and read any banner
4. save the JSON report to disk

The JSON report includes the target, timestamp, checked ports, and a list of open findings with service notes for human review.

### PyTorch migration

Old checkpoints `checkpoints/latest.pt` are **not compatible** with Keras and are **not converted automatically**. Preserve backups of old checkpoints and run a fresh training job without using `--resume` on the first TensorFlow run. The importers, corpus, and `data/*.bin` remain compatible.

## Tests

```bash
python -m unittest discover -s tests -v
```

The tests verify import format and protection, causal masking, output formatting, gradients, model saving/loading, text generation, and a short resumed training run. A GitHub Actions workflow runs tests on CPU.

## Organization

- `src/import_dataset.py`: canonical data import and validation.
- `src/data.py`: text corpus and train/validation split.
- `src/model.py`: causal Transformer in TensorFlow/Keras.
- `src/train.py`: training and checkpoints.
- `src/evaluate.py`: loss and byte-level perplexity.
- `src/generate.py`: local generation.
- `src/content_safety.py`: shared secret-content filter.
- `src/ingest_url.py`: backward-compatible adapter for URL imports.
- `tests/`: unit and integration tests.
- `corpus/`: didactic examples.

The `corpus/imported/`, `data/`, and `checkpoints/` directories are ignored by Git. Byte-level perplexity **does not measure** vulnerability-analysis capability. Review any generated code before executing it.
