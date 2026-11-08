# Vorpal technical reference

Vorpal is an educational autoregressive model project **trained from scratch**, experimentally specialized in programming data and defensive cybersecurity. The training and inference backend is **TensorFlow/Keras on CPU**, without PyTorch or pre-trained models.

> Research prototype: default context window of only 512 **bytes** and a small parameter count. It is not a reliable vulnerability detector and does not replace security review.

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

## Local web interface

With the virtual environment active, start the dashboard:

```bash
python src/web.py
```

For development with automatic browser refresh and backend restart:

```bash
python src/web.py --reload
```

Frontend edits in `web/` refresh the page on the next dashboard poll. Python edits
in `src/` and JSON edits in `config/` restart the server once dashboard jobs finish.
Unsaved form input can be lost on refresh. Checkpoint, corpus and log writes do not
trigger reload. The regular command keeps automatic reload disabled.

Open **http://127.0.0.1:8765** in your browser. Use `--port 8766` to choose another port. The server works from any current directory and runs project commands from the repository root with the same Python interpreter used to start it. There are no additional dependencies, frontend build steps, external fonts or external services.

The dashboard provides:

- **Overview:** actual checkpoint progress, prepared document counts, corpus size, validation history and recent activity. Missing metrics are shown as empty states.
- **Generate code:** prompt, checkpoint, byte count, temperature and CPU threads, with copyable raw output. Generated code is never executed.
- **Training:** new documents or `--replay`, corpus profiles, steps, batch size, live output and interruption. Starting training updates the normal project checkpoints; there is no automatic training on page load and no reset button.
- **Dataset:** corpus preparation, import of local files and URLs already listed in the corpus, document counts, duplicate counts, PDF search and PDF viewing.
- **Evaluation:** held-out loss/perplexity and the fixed syntax/behavior benchmark. Results identify the evaluated dataset and sample count.
- **Tools:** ask the checkpoint to propose Nmap/Nuclei arguments, inspect the command/reason, explicitly authorize the target and run the reviewed proposal.
- **Configuration:** edit the existing JSON files with the same validation used by the CLI. Changes apply to future tasks. Restart the server after changing `web.json`.

`config/web.json` controls the port, corpus/output profiles, input limits, polling, log size and retained activity. The shared subject profiles are the full corpus, security, Python, Nmap and Nuclei. The training CLI selects the same entries with `--profile`. CLI defaults for other commands stay in their respective files. Only one task runs at a time **within the dashboard**, and settings cannot be saved while a task runs. Full task output and activity metadata persist in `logs/web/`; the browser shows a bounded tail of the output.

The server binds only to loopback and checks request Host, Origin and a per-process mutation token. It exposes fixed project actions rather than arbitrary shell commands. It is a local development tool. Use **Ctrl+C** in the server terminal to stop it; an active task is interrupted while preserving its last saved checkpoint.

## Configuration

Runtime defaults are read from the JSON files in `config/`; no extra dependency is needed. Edit the relevant file and restart the command. Existing command-line arguments override the file defaults. Configuration files are located relative to the repository, so commands can run from another working directory; relative paths **inside** configuration retain their meaning relative to the working directory.

| File | Settings |
| --- | --- |
| `config/security_tools.json` | Nmap/Nuclei planning, allowed arguments, execution bounds and reviewed template fingerprints |
| `config/web.json` | Local dashboard server, corpus profiles, task limits, logs and polling |
| `config/tensorflow.json` | TensorFlow C++ log level before import (0: all, 1: warnings/errors, 2: errors, 3: fatal only) |
| `config/train.json` | Training defaults, paths, gradient clipping, validation/logging/save intervals and best checkpoint names |
| `config/model.json` | Model API defaults, feedforward size multiplier and CPU threading |
| `config/generate.json` | Generation CLI/API defaults, prompt, sampling, seed and checkpoint |
| `config/benchmark.json` | Fixed prompts, boundary cases, generation settings and code-quality report output |
| `config/evaluate.json` | Validation input, checkpoint, samples, threads and sampling seed |
| `config/data.json` | Corpus/output paths, extensions, directory/name filters, minimum document length and validation split |
| `config/import_dataset.json` | Import output, supported extensions, filters, archive/redirect limits, download timeout and user agent |
| `config/ingest_url.json` | Legacy URL adapter destination |
| `config/content_safety.json` | Shared secret-content filter pattern |
| `config/local_vuln_scan.json` | Default ports, timeout/output/checkpoint paths, banner limits, HTTP probe ports, inference limits and risk thresholds |
| `config/prompts.json` | Service/CVE instructions and risk comparison templates |

`cli` contains command defaults, `functions` contains defaults for direct Python API calls, `policy` contains filtering/selection rules, and `runtime` contains internal tunable limits. When both CLI and API expose the same option, their defaults can be set independently. Model architecture defaults used by training are in `train.json`; `model.json` controls direct `build_model()` calls. Architecture changes must match checkpoint metadata or require `--reset`.

For example, change `policy.default_ports` in `config/local_vuln_scan.json` to select scan ports, `cli.steps` in `config/train.json` to change training duration, or the instructions in `config/prompts.json` to change model prompts. This does not train the checkpoint; learned knowledge still comes from the corpus and training workflow.

`src/config.py` validates files against the type, key and range rules in `config/schema.json` and reports missing files, unknown keys and invalid values. The schema is the loader's validation specification, not a set of defaults. Authorization remains an explicit `--authorized` action. Byte vocabulary, binary formats, protocol validation and report field names remain code contracts.

On Linux, `requirements.txt` selects `tensorflow-cpu`. If an existing environment contains the `tensorflow` GPU-capable distribution, migrate it once using:

```bash
python -m pip uninstall -y tensorflow
python -m pip install -r requirements.txt
```

The entry points select CPU before importing TensorFlow. The default C++ log level is `2` (errors remain visible); set `TF_CPP_MIN_LOG_LEVEL=0` when debugging startup. CPU builds avoid CUDA driver initialization messages rather than hiding them.

## Dataset import — preserved

Corpus files named `scraping` also enable bounded same-host crawling during normal
import/training. See [the README](../README.md#scrape-pages-from-one-domain) for
the file format, incremental behavior, robots handling and `config/scraping.json`.

The importer remains **independent of the training backend** and accepts source code (.py, .js, .ts, .java, .kt, .go, .rs, .c, .cpp, .sh, .nse), documentation (.pdf, .docx, .md, .rst, .html, .txt), data (.json, .jsonl, .csv, .xml, .toml, .yaml), and security files (.sarif, .nessus, .har), including ZIP/TAR.GZ projects.

```bash
python src/import_dataset.py --file ./document.pdf
python src/import_dataset.py --directory ./my-data
python src/import_dataset.py --url https://example.org/documentation.html
```

The standalone importer defaults to the legacy `corpus/imported/` cache. For a subject-specific import, set its destination explicitly, for example:

```bash
python src/import_dataset.py --file ./nmap-guide.pdf --out corpus/tools/nmap/imported
```

Place local PDFs under the chosen subject's `pdfs/` directory. `src/train.py --profile nmap` imports that subject automatically before preparing its dataset. `src/data.py` alone only prepares text that is already present; it does not download URLs or extract PDFs.

### `url` files inside folders

Inside the selected subject's corpus directory or a supported subfolder, place a file named exactly **`url`** (no extension), UTF-8 encoded, with one public HTTP(S) URL per line:

```text
# authorized documentation
https://docs.python.org/3/library/secrets.html
https://example.org/guide.pdf
```

`--directory` imports local documents, compressed archives, and URLs from supported subfolders, skipping ignored directories and generated `imported/` caches. Duplicate URLs are deduplicated within the same run. Failures are recorded in `<out>/import_report.json` without stopping other downloads; with the standalone defaults, this is `corpus/imported/import_report.json`. The `url` file is not turned into training text. The importer prints each file and URL as it is processed.

**Security:** validate licensing and authorization, review personal data and secrets; the filters are heuristic. There is no per-file, download, URL-list, or PDF-page size limit, so only use trusted sources and ensure sufficient memory and disk space. Archives still have member-count and compression-ratio safeguards. Blocking private addresses by DNS verification does not eliminate DNS rebinding risk; use network isolation with untrusted sources. Downloaded content is never executed and OCR is not performed.

## Portuguese data from TensorFlow Datasets

For automatic discovery through corpus `tfds` files and the normal training
command, see [the current dataset workflow](WIKI40B_TRAINING.md#automatic-corpus-workflow).
The standalone export workflow below remains supported.

Wiki40B Portuguese can train the **same Vorpal model**, preserving official
train/validation/test splits. Install `requirements-tfds.txt`, then run
`python src/prepare_wiki40b.py`. See [the Wiki40B workflow](WIKI40B_TRAINING.md)
for source licensing, filtering, mixed replay, `train.py --prepared --resume`,
evaluation and limitations. Dataset downloads and exports stay under `data/`.

## Prepare, train, evaluate, and generate

```bash
python src/train.py --steps 500 --batch 8 --threads 4
python src/evaluate.py --samples 50 --threads 4
python src/generate.py --prompt "def validate_port(" --max-new 160 --threads 4
```

Without `--profile`, these commands use the complete corpus and the default `data/` output. `src/train.py` imports new supported files and public URLs listed in `url` files under the selected corpus, prints each source to the console, prepares only documents not previously used, and automatically resumes the existing checkpoint. Imports and failures are recorded in `<corpus>/imported/import_state.json` and `<corpus>/imported/import_report.json`; successful extracted text is included in the selected dataset.

Each run's `--steps` value is the number of additional optimization steps when there are documents to train. Use `--replay` to include already-consumed documents. `--reset` explicitly reimports the selected corpus and rebuilds the shared latest model from step zero; see the reset scope below.

To prepare data without starting training, run `python src/data.py --corpus corpus --out data`. Outputs `data/train.bin` and `data/valid.bin` keep the **same byte format as before**. Training uses byte-level tokenization (256 possible byte values), causal attention, AdamW, and gradient clipping.

To ask for security tips in Portuguese, open **Ask / generate**, select
**Security question (Portuguese)** and click **Generate response**. Example buttons
fill questions about phishing and passwords. From the terminal:

```bash
python src/generate.py --question "Como proteger minhas contas contra phishing?" --max-new 800 --threads 4
```

Question mode adds a Portuguese question/answer prompt and prints only the continuation.
Each question is independent, using the same local checkpoint without conversation
memory. Useful answers require training on relevant Portuguese question/answer examples;
this mode alone does not add security knowledge or guarantee accurate advice.
Reviewed examples can be added to `corpus/security/` for the Security training profile.

For a focused Brazilian Portuguese experiment, `corpus/pt_br/` contains 48 short
security question/answer examples and six security reading texts. Its `linguagem/`
subfolder adds 30 general reading texts, 40 standalone question/answer examples,
and ten short dialogues covering everyday language, grammar, comprehension and
clarification. These are locally authored teaching examples, not an independent
language benchmark or a pretrained language model. The collection is small and
includes related topics across documents, so validation loss alone does not show
generalization. Train it explicitly with:

```bash
python src/train.py --corpus corpus/pt_br --out data/pt_br --steps 500 --threads 4
```

This command updates the shared checkpoint and requires its architecture to match
the training configuration. Preserve checkpoints before an intentional `--reset`.
The dialogue files are training text; question mode still has no conversation memory.

An additional 209 chapter groups from three public-domain Brazilian novels are
available in `corpus/pt_br/literatura/`. They retain historical spelling. See
[the PT-BR training experiment](PT_BR_TRAINING.md) for provenance, processing,
training commands and evaluation limitations.

Generation prints the supplied prompt followed by its continuation. `--max-new 160` samples 160 new byte tokens, not 160 words; the CLI does not save or execute the generated code.

Each training command saves console output and Python errors to a timestamped file under `logs/` while continuing to display them in the terminal. Use `--log-dir PATH` to choose another log folder.

With default paths, successful imports are recorded in `corpus/imported/import_state.json` and consumed document hashes in `data/training_state.json`. For subject profiles these files are `<corpus>/imported/import_state.json` and `<out>/training_state.json`, respectively. Later runs skip the same URLs, unchanged local files, and documents already consumed by training; changed files are imported and trained as new content. Failed imports are retried on the next run.

Default architecture: 3 Transformer blocks, width 96, four attention heads, context of 512 bytes. The longer context lets the model use more surrounding code, while increasing attention compute and memory. Arguments `--dim`, `--heads`, `--layers`, `--context`, `--batch`, and `--steps` allow reducing CPU/RAM usage. Existing checkpoints with the old context are incompatible; run once with `--reset` to start the new architecture from scratch.

The checkpoint is stored in `checkpoints/latest.keras` (model and optimizer state) and `checkpoints/latest.json` (configuration and cumulative step). It resumes automatically when present. `--resume` remains available to explicitly require a checkpoint.

```bash
python src/train.py --steps 1000 --batch 8 --threads 4
```

When resuming, the architecture configuration must be identical. Model and optimizer state are restored. New checkpoints also restore the NumPy byte-window sampling state; legacy checkpoints without that field use a seed derived from the cumulative step. This does not guarantee bit-for-bit TensorFlow reproducibility. An explicit complete-corpus rebuild is `python src/train.py --profile complete --reset --steps 500 --batch 8 --threads 4`.

## Training by subject

The corpus is organized by subject. Each subject has its own prepared dataset, import cache and consumed-document/split history:

```text
corpus/
  security/
    03_security_checklist.md
    url
    pdfs/                      # Security reference PDFs (local, ignored)
    imported/                  # Extracted security text (generated, ignored)
  python/
    01_secure_python.md
    examples/                  # Reviewed Python exercises and secure-code example
    pdfs/                      # Python books/reference PDFs (local, ignored)
    imported/
  tools/
    nmap/
      examples/                # Model command-planning examples for Nmap
      pdfs/                    # Nmap reference PDFs (local, ignored)
      imported/
    nuclei/
      examples/                # Model command-planning examples for Nuclei
      imported/
```

Select the same subjects from the web dashboard or with `--profile`:

| Profile | Corpus | Prepared data |
| --- | --- | --- |
| `security` | `corpus/security` | `data/security` |
| `python` | `corpus/python` | `data/python` |
| `nmap` | `corpus/tools/nmap` | `data/tools/nmap` |
| `nuclei` | `corpus/tools/nuclei` | `data/tools/nuclei` |
| `complete` | `corpus` | `data` |

Project code, documentation, CLI messages, and the dashboard use English. The previous `seguranca` profile ID remains a compatibility alias for `security`, configured in `config/web.json`. Subject folders use `security`, `tools`, and `examples`; existing local files and dataset histories have been moved to those paths. Translated planning examples receive new content hashes when the dataset is next prepared. Existing checkpoints and downloaded reference documents are preserved.

For a new model, train a first subject normally. Later subjects resume the same checkpoint automatically:

```bash
python src/train.py --profile security --steps 500 --threads 4
python src/train.py --profile python --steps 500 --threads 4
python src/train.py --profile nmap --steps 500 --threads 4
python src/train.py --profile nuclei --steps 500 --threads 4
```

Each command trains only unconsumed documents in the selected subject. Add `--replay` to continue learning from documents already used; it requires an existing checkpoint. These are separate phases of the **same model**, not separate model files. `latest.keras`/`best.keras` keep the configured shared paths; validation results from different subjects are not directly comparable. The profile registry is in `config/web.json`. Explicit `--corpus` and `--out` override profile defaults, and existing commands without a profile still use `config/train.json`.

`--reset` removes the configured shared `latest.keras` and its metadata, clears only the selected output's `training_state.json`, and forces reimport for the selected corpus. Existing split assignments and other profiles' histories remain. Resetting one subject therefore resets the shared model, not a separate subject-specific model. `best.keras` and the metrics history are shared artifacts too; the next applicable validation determines the best checkpoint for its comparison context.

Evaluation does not accept `--profile`; pass the matching binary with `--data`, for example:

```bash
python src/evaluate.py --data data/security/valid.bin --samples 50 --threads 4
python src/evaluate.py --data data/tools/nmap/valid.bin --samples 50 --threads 4
```

During this workspace's one-time reorganization, original document bytes were preserved, existing extracted text was copied into the appropriate subject cache, and subject histories were seeded from the prior global training/split state. Selecting a profile in a fresh output directory does not automatically perform that historical migration. The legacy `corpus/imported/` cache and `data/` remain available to the complete profile. Nested import caches are not reimported as source files. Document selection prefers already-known hashes over whitespace-equivalent copies, so moving a source behind its cached copy does not silently change its identity. Existing checkpoints are kept; organizing folders does not retrain or erase learned weights.

## Improve training and measure generated code

Internal metadata (`import_state.json`, manifests, reports and training/split state) is excluded both during import and dataset preparation. File-name exclusions are in `config/data.json`. Exact documents and copies differing only in trailing whitespace are deduplicated while retaining the existing hash format. Existing checkpoints are not rewritten by dataset preparation; they may still reflect previously ingested metadata.

`<out>/split_state.json` (`data/split_state.json` with default paths) persists train/validation assignments by document hash, initially migrating the existing manifest if present. Adding, renaming or replaying unchanged documents does not move validation examples into training. Keep this file with the dataset. A different output directory starts a separate split history; do not reuse it to claim an independent evaluation of an already trained checkpoint. Missing/very short validation data is reported and skips automatic validation.

To continue learning from the **same corpus** without deleting the checkpoint or re-downloading unchanged sources:

```bash
python src/train.py --replay --steps 500 --batch 8 --threads 4
```

`--replay` requires the existing checkpoint and metadata, resumes automatically and is mutually exclusive with `--reset` and `--resume`. Normal training still selects only new documents. New checkpoints also store the NumPy sampling state, so resumed runs continue their byte-window sequence; legacy checkpoints use a seed derived from the cumulative step. This does not promise bit-for-bit TensorFlow reproducibility.

Six reviewed Python exercises and an additional secure-code example are provided in `corpus/python/examples/`. The Python profile also includes its local books and other imported Python material. To fine-tune the existing model on that whole subject, use:

```bash
python src/train.py --profile python --replay --steps 500 --threads 4
python src/evaluate.py --data data/python/valid.bin --samples 50 --threads 4
```

The bundled exercises are educational examples, not evidence of general coding ability; the profile's actual size depends on locally imported material. It uses the same checkpoint paths as other training; copy the checkpoint and metadata first if you want to keep a separate experimental model. No long training run is launched automatically by these changes.

At the configured validation interval and final step, training records the current batch loss and fixed-window validation loss in `checkpoints/metrics.jsonl`. `latest.keras` continues to hold the latest model/optimizer state. `best.keras` and `best.json` retain the lowest validation loss; a resumed checkpoint is measured first when no comparable best score exists. Scores are compared only when validation bytes, architecture, sample count and seed match. Use `--validation-data PATH` to override the validation binary; missing explicit files fail clearly. An incremental subset or a changed held-out corpus starts a new comparison, so use a fixed replay corpus for learning curves.

Compare the same generation prompts and boundary cases before and after a training cycle:

```bash
python src/benchmark.py --output reports/before.json
# Run a training cycle, then:
python src/benchmark.py --output reports/after.json
python src/benchmark.py --checkpoint checkpoints/best.keras --output reports/best.json
```

The benchmark records full generated text, `ast.parse` syntax validity and behavior on fixed cases for `validate_port`, `is_even` and `clamp`. A limited AST interpreter checks returns, conditions, comparisons, small arithmetic, type checks and explicit errors. It never executes generated Python; imports, attributes, loops and other unsupported constructs are reported as `unsupported`. These exercises overlap the teaching corpus and measure task learning, not independent generalization or cybersecurity capability. There is no guarantee of coherent output after a particular number of steps.

`examples/service_training.jsonl` stages reviewed service-identification and unknown-CVE response examples outside the automatic corpus. Expand and verify this data separately only after the basic code tasks improve; prompts or JSON-shaped examples do not make generated CVEs factual.

## Local network report

For explicit local testing on a private IPv4 address, the project includes a bounded report generator that checks common ports and writes a JSON file. It does not execute payloads or rely on shell commands. It only accepts private or link-local IPv4 addresses and requires the `--authorized` flag.

```bash
./vorpal.py 192.168.1.25 --authorized --ports 22,80,443,3306,5432 --output reports/local_scan.json
./vorpal.py 192.168.1.25 --authorized --checkpoint checkpoints/latest.keras --output reports/local_scan.json
```

Service names and review notes are generated by the local checkpoint from the TCP port and banner, without a fixed service table. Missing checkpoints or invalid model responses produce `unknown` with an explanatory note. Generated identifications require human verification.

When a trained checkpoint is available, the scanner uses the model's learned language patterns to compare risky and safe service descriptions before classifying exposure. If no checkpoint is present, it falls back to conservative port-based heuristics and logs that limitation clearly.

The console prints the full flow step by step:

1. validate the target IP
2. enumerate the selected ports
3. test each port and read any banner
4. save the JSON report to disk

The JSON report includes the target, timestamp, checked ports, and a list of open findings with service notes for human review.

CVE suggestions are generated locally by the trained checkpoint from the service banner, with no external API, fixed product fingerprints, or built-in CVE database. The report records `cve_check.source` as `model` and `verified` as `false`. Status is `unverified_suggestions`, `unknown` (no suggestions), `not_checked` (missing model/banner), or `unavailable` (invalid output or inference failure). Suggestions depend on the training corpus and may be incorrect or outdated; an empty list does not establish absence of vulnerabilities. Review and independently verify every suggested CVE before drawing conclusions.

## Nmap and Nuclei with model-proposed commands

Nmap and Nuclei are optional system executables, discovered through `PATH`; they are not Python packages or automatically installed. Their option syntax was checked against the installed versions and the primary references: [Nmap manual](https://nmap.org/book/man-briefoptions.html) and [Nuclei documentation](https://github.com/projectdiscovery/nuclei).

In the web dashboard, open **Tools**, select a tool/checkpoint, supply a local target and a concrete goal, then request a proposal. The checkpoint generates JSON containing `args` and `reason`. The application validates these arguments, binds the user-supplied target, adds the execution policy, and displays the resulting command. No scanner runs during planning. To execute, review the exact command and explicitly authorize the displayed target.

The same flow is available from the CLI:

```bash
python src/security_tools.py plan --tool nmap --target 192.168.1.10 --goal "Identify web and SSH services" --threads 4
python src/security_tools.py plan --tool nuclei --target http://192.168.1.10:8080 --goal "Review HTTP headers" --threads 4
```

A valid proposal prints a separate `run` command containing its saved plan ID and digest. Copy that command only after reviewing it; execution requires `--authorized`. Plans and execution reports are written under `reports/security_tools/`, while the dashboard also retains its normal task logs. Each execution revalidates the arguments, policy, binary location and template fingerprints. A changed command/configuration or a mismatched digest requires a new proposal. Tool process exit success is not proof that a service is secure.

Targets are restricted to a single literal RFC1918/loopback/link-local IPv4. Nuclei requires an HTTP(S) origin on such an address, without credentials, paths, queries or fragments. Hostnames, CIDR blocks, multiple targets, shell syntax, NSE scripts, output-file options, arbitrary template paths, remote templates and cloud uploads are outside the supported model argument catalog. Execution uses an argument array without a shell and enforces a timeout; output/report sizes are bounded in memory.

`config/security_tools.json` contains the planner instructions, generation parameters, allowed option subsets, limits, required execution flags and local template registry. The model selects the supported scan arguments; target binding and scope/execution constraints are deterministic validation, not model-generated commands. Nmap uses unprivileged TCP checks, bounded port selection/rate/retries and XML output. Nuclei runs only the explicitly selected, fingerprinted local HTTP templates with updates, external Interactsh callbacks and redirects disabled. The initial templates in `tools/nuclei/templates/` report a Server header and missing `X-Content-Type-Options: nosniff` as informational observations for manual review; they do not perform exploitation or establish CVE applicability. The isolated Nuclei configuration is fingerprinted too. Review source changes before updating its SHA-256 values; the application does not download new templates.

The current educational checkpoint may produce invalid JSON or unsuitable options. These are reported as rejected proposals, with the model response available for inspection. **There is no fixed-command fallback and no guarantee that the proposed command is optimal.** Twelve reviewed examples in `corpus/tools/nmap/examples/` and `corpus/tools/nuclei/examples/` use the planner's actual prompt/JSON format. To teach that format while continuing the existing checkpoint:

```bash
python src/train.py --profile nmap --replay --steps 500 --threads 4
python src/train.py --profile nuclei --replay --steps 500 --threads 4
```

This updates the existing checkpoint and does not guarantee correct proposals after a fixed number of steps. Training is not started automatically by tool planning. Long goals may be shortened to fit the checkpoint context; the actual goal used is recorded in the proposal.

### PyTorch migration

Old checkpoints `checkpoints/latest.pt` are **not compatible** with Keras and are **not converted automatically**. Preserve backups of old checkpoints and run a fresh training job without using `--resume` on the first TensorFlow run. The importers, corpus, and `data/*.bin` remain compatible.

## Tests

```bash
python -m unittest discover -s tests -v
```

The tests cover import validation, nested cache exclusions, subject selection and CLI overrides, stable document identities/splits, replay and resumed training, best checkpoints, generation, the AST benchmark, dashboard HTTP/task behavior and scanner command validation. A GitHub Actions workflow runs tests on CPU. Scanner unit tests do not require Nmap or Nuclei binaries; real scanner checks against an owned loopback fixture are separate integration checks.

## Organization

- `src/security_tools.py`: model-proposed, validated local Nmap/Nuclei command planning and execution.
- `tools/nuclei/`: reviewed HTTP templates and isolated Nuclei configuration.
- `src/web.py`: local dashboard HTTP server and background task runner.
- `web/`: responsive dashboard interface in HTML, CSS and JavaScript.
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

All `corpus/**/imported/` caches and `corpus/**/pdfs/` directories, plus `data/`, `checkpoints/`, `logs/` and `reports/`, are ignored by Git. Downloaded PDFs are local assets and are not included in a fresh clone. Source/rights notes and per-folder checksums are stored alongside the local PDFs; `corpus/SHA256SUMS` indexes their paths relative to `corpus/`. Byte-level perplexity **does not measure** vulnerability-analysis capability. Review any generated code before executing it.
