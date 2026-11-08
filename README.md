# Vorpal

Vorpal is an educational autoregressive model trained from scratch with **TensorFlow/Keras on CPU** for programming and defensive cybersecurity. It aims to support analysis of a specific IP on an authorized local network.

The default model uses 3 Transformer blocks, width 96, 4 attention heads and a context of **512 bytes**. It is experimental: generated code, service identifications and CVE suggestions require human review. Validation loss does not measure vulnerability-detection capability.

## Install

Use Python 3.11, preferably on Linux/WSL. About 8 GB of RAM is recommended for the default model.

```bash
git clone https://github.com/hebermattos/vorpal.git
cd vorpal
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt
```

## Web dashboard

```bash
python src/web.py
```

Open **http://127.0.0.1:8765**. The dashboard supports training, generation, dataset import, evaluation, configuration and reviewed Nmap/Nuclei proposals. One dashboard task runs at a time; logs persist in `logs/web/`.

Use `--port 8766` for another port or `--reload` for development. Reload refreshes frontend edits and restarts the backend after active jobs finish; unsaved form input may be lost. Stop the server with **Ctrl+C**.

## Train

Place documents in the selected corpus folder. For public HTTP(S) sources, add a UTF-8 file named `url` with one URL per line:

```text
# Reference documentation
https://docs.python.org/3/library/secrets.html
```

One command imports new supported files and listed URLs, prepares the dataset and trains:

```bash
python src/train.py --profile security --steps 500 --threads 4
```

| Profile | Corpus | Prepared data |
| --- | --- | --- |
| `complete` | `corpus` | `data` |
| `security` | `corpus/security` | `data/security` |
| `python` | `corpus/python` | `data/python` |
| `nmap` | `corpus/tools/nmap` | `data/tools/nmap` |
| `nuclei` | `corpus/tools/nuclei` | `data/tools/nuclei` |

Without `--profile`, defaults come from `config/train.json` (`corpus` and `data`). Explicit `--corpus` and `--out` override profile paths. Profiles share **one model checkpoint**, with separate datasets and document histories.

| Mode | Behavior |
| --- | --- |
| Default | Train only unconsumed documents; resume automatically if a checkpoint exists. |
| `--replay` | Include previously used documents; requires a checkpoint and metadata. |
| `--reset` | Reimport the selected corpus and rebuild the shared model from step zero. |
| `--resume` | Explicitly require an existing checkpoint. |

The three flags are mutually exclusive. Each `--steps` value adds that many optimization steps when eligible documents exist.

```bash
# Continue learning from previously used Nmap documents
python src/train.py --profile nmap --replay --steps 500 --threads 4

# Rebuild the shared model using the complete corpus
python src/train.py --profile complete --reset --steps 500 --threads 4
```

**Reset affects the shared model**, even when selecting one subject. It clears only the selected output's consumed-document state, preserving split assignments and other profiles' histories. Back up checkpoints before resetting.

Training saves the latest model/optimizer in `checkpoints/latest.keras`, metadata in `checkpoints/latest.json`, validation metrics in `checkpoints/metrics.jsonl` and console logs under `logs/` (`--log-dir` overrides this). `best.keras` retains the best comparable validation checkpoint. Resuming requires matching architecture settings and does not guarantee bit-for-bit reproducibility.

`--lr` (default from `config/train.json`) applies to both new and resumed runs, preserving optimizer moments and iteration count. For example, continue the Python phase at a lower learning rate:

```bash
python src/train.py --profile python --replay --lr 0.0001 --steps 500 --threads 4
```

Training maps binary datasets read-only instead of copying entire files into RAM; corpus preparation still needs memory for document processing. Loss logs describe each current batch. A non-finite training loss stops the run before saving that update, preserving the last saved checkpoint.

Keep `<out>/split_state.json` and `<out>/training_state.json` with each dataset to preserve held-out assignments and consumed-document history.

### Examples by training group

These commands train new documents and automatically resume the shared checkpoint. Run the groups you need; `complete` includes all subjects and is an alternative to selecting them individually.

```bash
# Python
python src/train.py --profile python --steps 500 --batch 8 --threads 4

# Security
python src/train.py --profile security --steps 500 --batch 8 --threads 4

# Languages: Brazilian Portuguese (explicit paths; no registered profile)
python src/train.py --corpus corpus/pt_br --out data/pt_br --steps 500 --batch 8 --threads 4

# Tools: Nmap
python src/train.py --profile nmap --steps 500 --batch 8 --threads 4

# Tools: Nuclei
python src/train.py --profile nuclei --steps 500 --batch 8 --threads 4

# Complete corpus
python src/train.py --profile complete --steps 500 --batch 8 --threads 4
```

To train a group again using previously consumed documents, add `--replay` to its command once a checkpoint and metadata exist. For example:

```bash
python src/train.py --corpus corpus/pt_br --out data/pt_br --replay --steps 500 --batch 8 --threads 4
```

### TensorFlow Datasets sources

Like `url`, a UTF-8 file named `tfds` lists one dataset identifier per line; blank lines and `#` comments are ignored. `corpus/pt_br/tfds` already contains:

```text
wiki40b/pt:1.3.0
```

Install dataset support once with `python -m pip install -r requirements-tfds.txt`. Then the language training command above downloads and prepares missing Wiki40B data, combines it with local documents and trains the same model. The `complete` profile also discovers this file. Initial preparation requires network access, disk space and memory for the dataset.

Unchanged prepared data is verified and reused from `data/wiki40b/pt`. Normal training skips consumed documents; `--replay` includes them again. `--reset` rebuilds the model and consumed-document state while reusing the verified source cache. Official validation stays held out and official test text never enters training. Conflicts with existing local split history fail explicitly.

`config/tfds.json` controls the list filename, download directory and adapter output paths. Supported identifiers:

| Identifier | Content |
| --- | --- |
| `wiki40b/pt:1.3.0` | Portuguese Wikipedia articles |
| `databricks_dolly:1.0.0` | Instructions, context and responses |
| `squad/v2.0:3.0.0` | Context/question/answer, including unanswerable questions |
| `natural_instructions:1.0.1` | Task definitions, inputs and outputs |
| `opus/en-pt_gnome:0.1.0` | Custom English–Portuguese GNOME translation configuration |

The new sources are **disabled by default**: uncomment selected lines in `corpus/instructions/tfds` or the OPUS line in `corpus/pt_br/tfds`. They are not included in the Nmap profile. Once enabled, normal corpus training prepares and imports them automatically. For instruction datasets, use `--corpus corpus/instructions --out data/instructions`.

`config/tfds_text.json` controls text templates, filtering, a default export limit of 10,000 source records per split and a 10% deterministic validation fraction for train-only datasets. The export limit does **not** limit the upstream TFDS download/preparation size. SQuAD preserves official splits and excludes held-out context groups from training. Dolly, Natural Instructions and OPUS use stable prompt/source-text groups for validation; these are local splits, not official benchmarks. Groups can share topics or tasks across splits.

Text exports retain manifests, checksums, indexes and upstream attribution notes. Review each source/subset license before redistribution. Changed export settings require a new output path in `config/tfds.json`; cached exports are preserved. Keep examples compatible with the model’s context length; these adapters do not increase the 512-byte window. See [the adapter guide](docs/TFDS_DATASETS.md) for formats and activation examples.

`src/data.py` alone still prepares only local text, and `--prepared` bypasses source discovery.

## Evaluate and generate

Evaluation takes a data path rather than a profile:

```bash
python src/evaluate.py --data data/security/valid.bin --samples 50 --threads 4
python src/generate.py --prompt "def validate_port(" --max-new 160 --threads 4
python src/benchmark.py --output reports/benchmark.json
```

Generation prints the prompt and continuation; `--max-new` counts bytes, not words. The benchmark checks syntax and limited AST behavior without executing generated Python. Its teaching tasks do not establish general coding ability.

For a standalone security question in Portuguese:

```bash
python src/generate.py --question "Como proteger minhas contas contra phishing?" --max-new 800 --threads 4
```

Question mode has no conversation memory and requires relevant training data. See the [PT-BR experiment](docs/PT_BR_TRAINING.md) and [Wiki40B workflow](docs/WIKI40B_TRAINING.md) for language training and prepared datasets. There is currently no language profile in the shared registry.

## Import data separately

The importer accepts code, documents (including PDF/DOCX), structured text and ZIP/TAR.GZ archives:

```bash
python src/import_dataset.py --file ./document.pdf --out corpus/security/imported
python src/import_dataset.py --directory ./my-data --out corpus/security/imported
python src/import_dataset.py --url https://example.org/guide.html --out corpus/security/imported
```

Without `--out`, imports go to `corpus/imported/`. Import state and failures are recorded in the destination's `import_state.json` and `import_report.json`. Later training runs retry failed imports and skip unchanged successful sources. Downloaded content is never executed; OCR is not performed.

Use trusted, appropriately licensed sources and review secrets/personal data. Filters are heuristic. Direct imports have no per-file, download or PDF-page size limits; ensure sufficient resources. Scraping has the separate response limits described below. Archives retain member-count and compression-ratio safeguards.

To prepare existing text without imports or training:

```bash
python src/data.py --corpus corpus --out data
```

### Scrape pages from one domain

Create a UTF-8 file named `scraping` in a corpus folder, with one starting URL per line. Blank lines and `#` comments are ignored. A commented template is available at `corpus/security/scraping`:

```text
https://example.org/docs/
```

Then use the normal training command:

```bash
python src/train.py --profile security --steps 500 --threads 4
```

The importer follows HTML links only on the **same origin and within the starting URL’s directory**. For example, `https://nmap.org/book/man.html` permits pages under `https://nmap.org/book/`, including subdirectories, but excludes `/download.html`, `/book-other/`, other domains, subdomains and scheme/port changes. Redirects and cached links follow the same restrictions. Use a trailing slash for directory seeds, such as `https://nmap.org/book/`. The origin’s `/robots.txt` is fetched separately to check crawling permissions; it is not imported as training content. JavaScript is not executed.

`config/scraping.json` controls the visited-page budget per starting URL, maximum depth (the starting page is depth 0), links per page, response byte limit and minimum request interval. Query links are disabled by default. The crawler respects `robots.txt`, crawl delays and request rates; it skips requests requiring a wait above the configured maximum. A missing robots file (404/410) permits crawling; other retrieval failures are reported.

Successful pages reuse the usual import/document history. Discovered links persist in `<corpus>/imported/scraping_state.json`, so later runs can reach pending pages without downloading successful pages again. Failures appear in `import_report.json` and retry on later runs. `--reset` crawls the source lists again. To refresh already imported pages or discover new site links without resetting the model, run `python src/import_dataset.py --directory corpus/security --out corpus/security/imported`, then train normally. Standalone directory imports refresh sources and their link cache.

## Analyze a local target

The built-in report generator accepts private/link-local IPv4 addresses and requires explicit authorization:

```bash
python vorpal.py 192.168.1.25 --authorized --ports 22,80,443 --output reports/local_scan.json
```

It checks TCP ports and banners and writes a JSON report. Model-generated service/CVE suggestions are unverified and may be incorrect or outdated. Without a checkpoint, risk classification falls back to port-based heuristics. An empty report does not establish that a target is secure.

Nmap and Nuclei are optional executables installed separately and available through `PATH`. Request a proposal from **Tools** in the dashboard or the CLI:

```bash
python src/security_tools.py plan --tool nmap --target 192.168.1.10 --goal "Identify web and SSH services" --threads 4
python src/security_tools.py plan --tool nuclei --target http://192.168.1.10:8080 --goal "Review HTTP headers" --threads 4
```

Planning does not run a scanner. Review the proposal, then use its printed `run` command with `--authorized`. Execution validates the saved plan, target, arguments and policy. Nuclei uses reviewed, fingerprinted local templates. Reports persist under `reports/security_tools/`. Invalid proposals are rejected without a fixed-command fallback. Only scan targets you are authorized to assess.

## Configuration and layout

Defaults, prompts and restriction policies live in `config/`. CLI options override defaults. The profile registry is `config/web.json`; training architecture defaults are in `config/train.json`. Configuration is validated against `config/schema.json`. Run CLI examples from the repository root so relative data paths resolve as shown.

| Location | Purpose |
| --- | --- |
| `src/` | Import, data preparation, model, training, evaluation, generation and security tools |
| `web/` | Local dashboard frontend |
| `corpus/` | Training sources organized by subject |
| `tests/` | Automated tests |
| `docs/` | Detailed workflows and technical reference |

Generated import caches, local PDF folders, `data/`, `checkpoints/`, `logs/` and `reports/` are ignored by Git. Local PDFs are not included in a fresh clone. Legacy PyTorch checkpoints cannot be loaded or automatically converted to Keras.

## Tests and further details

```bash
python -m unittest discover -s tests -v
```

Scanner unit tests do not require Nmap/Nuclei binaries. See the [technical reference](docs/REFERENCE.md) for configuration fields, import safeguards, checkpoint comparisons, stable document identities, scanner restrictions and migration notes.
