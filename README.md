# Vorpal

Vorpal is an educational **local security-analysis** project for interpreting saved Nmap/Nuclei results from an explicitly authorized private-network target. The default setup uses **Ollama + a local RAG index**; llama.cpp is also supported. It proposes unverified hypotheses and checks; it does not confirm vulnerabilities or execute suggested commands.

Vorpal also includes a **separate experimental TensorFlow/Keras Transformer trained from scratch on CPU** (3 blocks, width 96, 4 attention heads, 512-byte context). Its checkpoints, training, evaluation and generation workflows remain available but are **not required** for the Llama + RAG analysis path.

## Install

Use Python 3.11, preferably on Linux/WSL. Llama GGUF RAM requirements depend on the chosen model and quantization; about 8 GB is a reference only for the small experimental Keras model.

```bash
git clone https://github.com/hebermattos/vorpal.git
cd vorpal
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt
```

The default dependencies and `scripts/setup_ollama_rag.sh` do not install
TensorFlow. For an existing environment dedicated to Ollama + RAG, remove it with:

```bash
python -m pip uninstall tensorflow tensorflow-cpu
```

Ollama supplies the analysis model. Hybrid RAG uses its own Sentence Transformers
embedding model and CPU PyTorch, not TensorFlow. TensorFlow checkpoints are not
used by this workflow and can be kept as archives. To use the optional Keras
training/evaluation/generation commands later, install
`python -m pip install -r requirements-training.txt`. The optional
`requirements-tfds.txt` includes those training dependencies as well.

## Default analysis: local Llama + RAG

The normal workflow is **saved scanner output → RAG evidence → local Llama → review-only report**. Llama and the embedding model (if used) are different models. No training or cloud inference is required.

1. Install `llama.cpp` separately (build or install its `llama-server` executable) and obtain a **compatible, licensed, pretrained Llama Instruct GGUF file** from a source you trust. Review RAM/CPU requirements; the 8 GB guideline above applies to the small *experimental Keras model*, not necessarily to your GGUF.
2. Start the Llama server using the actual path to your GGUF file. The alias `local` must match `config/scan_intelligence.json`:

```bash
llama-server \
  -m /path/to/your-instruct-model.gguf \
  --alias local \
  --host 127.0.0.1 \
  --port 8080
```

Alternatively, install [Ollama](https://ollama.com/download) and start its
[OpenAI-compatible local server](https://docs.ollama.com/api/openai-compatibility):

```bash
python src/run_ollama.py
```

The launcher reads the port from `config/scan_intelligence.json` and binds only
to `127.0.0.1` (default `8080`), overriding an inherited `OLLAMA_HOST`. Run it
instead of `llama-server` on that port. Stop it with **Ctrl+C**; server logs are
shown in the terminal. It does not install Ollama or download models.

In a second terminal, use the same endpoint to list installed models:

```bash
OLLAMA_HOST=127.0.0.1:8080 ollama list
```

Set `model` in `config/scan_intelligence.json` to an installed local Instruct
model's exact name, or copy that model to the default alias with
`OLLAMA_HOST=127.0.0.1:8080 ollama cp YOUR_INSTALLED_MODEL local` (replace
`YOUR_INSTALLED_MODEL` with its name). If no model is installed, explicitly
download your chosen model with `ollama pull` using the same `OLLAMA_HOST`.
Use the configured port in these commands if you change it. An Ollama service
already listening on its usual port `11434` is a separate endpoint.

3. From the repository root, update RAG using your **reviewed local documentation**. This indexes the *entire* `corpus/` tree into the configured default database, without importing URLs:

```bash
python vorpal.py knowledge update
```

4. Analyze an **existing Nmap XML** file containing the exact authorized private IPv4 target:

```bash
python src/scan_intelligence.py \
  --target 192.168.1.10 \
  --authorized \
  --nmap-xml reports/nmap.xml \
  --output reports/scan_analysis.json
```

You can additionally supply `--nuclei-jsonl reports/nuclei.jsonl`. The analysis module does **not** perform scans. It requires an existing RAG database and at least one relevant retrieved document. If retrieval is empty, it stops rather than silently running without RAG.

The server endpoint is fixed to `127.0.0.1`; proxies and redirects are disabled. The report contains scanner observations, referenced documents (source path, line and SHA-256), *unverified* hypotheses and suggested follow-up checks. Each hypothesis must cite a retrieved document ID, but a valid ID **does not prove** that the cited text supports the claim. Follow-up arguments are validated by the deterministic Nmap/Nuclei policy and are **never automatically executed**. Human review is mandatory.

**Troubleshooting:** If the server is unavailable, confirm `llama-server` is running on port 8080 and the configured model alias is `local`. If RAG is missing or empty, review the documents under `corpus/` and re-run `python vorpal.py knowledge update`. If the model returns invalid JSON, the analysis rejects it; use a compatible Instruct model and review its output. Do not treat model text or retrieved documents as trusted instructions.

## Knowledge updates (no model training)

For Linux/WSL, the complete setup script creates/reuses `.venv`, installs the
project requirements, installs Ollama if missing using its
[official installer](https://docs.ollama.com/linux), starts/reuses the configured
loopback server, prepares the local model, and indexes the corpus:

```bash
bash scripts/setup_ollama_rag.sh --import-sources
```

This downloads packages and model weights; the Ollama installation may request
sudo. `--import-sources` also processes the corpus PDF/URL/scraping inputs through
the existing importer. Omit it to index only existing local text. No training or
scanning is started. Defaults are in `config/setup_ollama_rag.json` (local Ollama
model, embedding model ID and startup timeout); the endpoint and analysis model
alias come from `config/scan_intelligence.json`. Existing models and aliases are
reused. Setup logs and server logs are saved under `logs/setup/`; knowledge audit
reports remain under `logs/knowledge/`. A server started by setup stays running
on success; the script prints its PID and stop command. A server it started is
stopped if setup fails. Other server instances are left running.

To include hybrid retrieval dependencies and install the
[multilingual embedding model](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2):

```bash
bash scripts/setup_ollama_rag.sh --hybrid --import-sources
```

Hybrid setup downloads CPU PyTorch, Sentence Transformers and embedding weights
into the configured `embedding_model` directory. Existing embedding directories
are validated locally and preserved. A text-only index cannot be converted in
place: choose a new index using `--database data/rag/hybrid.sqlite3`, then pass
that path with `--rag-db` during analysis. To make it the default, set `database`
consistently in `config/knowledge.json` and `config/rag.json`. Setup preserves
the existing index when a mode conflict is reported. Do not run setup/indexing
concurrently with another indexing process.

The Knowledge Manager updates the **existing local RAG corpus** and writes an
incremental JSON audit report. It does not change TensorFlow checkpoints,
training history, splits or GGUF model weights.

```bash
# Default: index already available local UTF-8 corpus files, no source downloads
python vorpal.py knowledge update

# Opt in to the existing importer (local files, URL lists and bounded scraping
# according to existing source configuration; this may access public websites)
python vorpal.py knowledge update --import-sources

# Optional: use a previously installed local embedding model
python vorpal.py knowledge update --hybrid
```

Defaults live in `config/knowledge.json`: `corpus` is the **entire**
`corpus/` tree and the index is `data/rag/index.sqlite3`. Do not reuse the
same database with a different corpus: the command checks the index root
before updating to prevent accidental removal of other categories.

Each update compares document hashes and reports added, updated and removed
sources, current document/chunk counts, and SQLite integrity. Run reports are
saved in `logs/knowledge/update_*.json`. Already indexed content is reused;
the command does not claim retrieval relevance or model improvements. The
`--import-sources` flag uses existing import history and retries failures.
Previously accepted URL entries are **not automatically re-fetched**.

Updating RAG only changes the evidence retrieved during the default Llama analysis;
it **does not fine-tune the local llama.cpp model** or start Vorpal's Keras
training pipeline. Source content is still untrusted and secret filtering is
heuristic. The update command may use the network only with
`--import-sources`, so review your corpus URL/scraping lists beforehand.

## RAG modes and corpus isolation

The **default** retrieval index uses SQLite FTS5/BM25 (with a slower Python BM25 fallback when FTS5 is missing). It is *text-only* unless you explicitly enable hybrid retrieval. Incremental updates reuse unchanged document hashes, reindex changed documents and remove deleted documents.

```bash
# Recommended: index the entire corpus with the Knowledge Manager
python vorpal.py knowledge update

# Search the default index; results include paths, line numbers and hashes
python src/rag.py search "Nmap TCP service identification"
```

**Optional hybrid mode** combines BM25, CPU-local embeddings, reciprocal-rank fusion and lexical coverage reranking. Install `sentence-transformers` separately, place a compatible local embedding model at `models/embeddings` (or configure `config/rag.json`) and use:

```bash
python vorpal.py knowledge update --hybrid
```

The embedding model is **not** the Llama GGUF model. No embedding model is downloaded automatically. An existing text-only index cannot be switched in place to hybrid mode: choose a **new database path** (`--database` on Knowledge Manager), and point `config/rag.json` at that database for subsequent queries and analysis.

**Do not use** `python src/rag.py index --corpus corpus/security` against the default whole-corpus database: direct index sync removes entries outside the selected corpus. For a deliberately isolated subset, use a separate database instead:

```bash
python src/rag.py index --corpus corpus/security --database data/rag/security.sqlite3
python src/rag.py search "HTTP security headers" --database data/rag/security.sqlite3
```

For subset analysis, pass `--rag-db data/rag/security.sqlite3` to `src/scan_intelligence.py`. Do not reuse the same database for distinct corpus roots. Secret filtering is heuristic: review documents and protect the index. Indexing does not execute downloaded content, train models or run scanners.

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

## Web dashboard

```bash
python src/web.py
```

Open **http://127.0.0.1:8765**. The dashboard supports training, generation, dataset import, evaluation, configuration and reviewed Nmap/Nuclei proposals. One dashboard task runs at a time; logs persist in `logs/web/`.

Use `--port 8766` for another port or `--reload` for development. Reload refreshes frontend edits and restarts the backend after active jobs finish; unsaved form input may be lost. Stop the server with **Ctrl+C**.

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

Keep `data/shared_split_state.json` with the shared checkpoint, and keep each
profile's `<out>/split_state.json` and `<out>/training_state.json`. The shared
registry preserves document assignments across profile changes, replay and reset,
including when using a custom `--out`. Each profile still has its own binaries
and consumed-document history. Recognized TFDS test documents stay out of both
training and validation binaries.

Vorpal checks existing split histories in the configured data directory and
registered profile outputs. A hash already assigned to training and held out
stops preparation, and the dataset audit reports the conflict without changing
files. Back up the histories and review the reported hashes before reconciling
them. If a checkpoint may have trained on a held-out document, evaluate it on a
fresh holdout. For old custom outputs outside the data directory, include their
histories in the review. Vorpal checks them when you select them.

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

## Optional supervised instruction fine-tuning

The default autoregressive training mode remains unchanged. To specialize an existing
checkpoint for reviewed scanner planning, use the **same training entry point**:

```bash
python src/train.py --instruction-examples examples/planner_instructions.jsonl --steps 100 --threads 4
```

This mode requires the existing shared Keras checkpoint and matching architecture. It does
not import source data, execute scanners, reset the model, or change existing binary datasets.
It uses response-only loss masking (prompt/padding positions carry zero weight), a configurable Nmap → Nuclei → mixed-tool curriculum with tool-balanced mixed sampling, and separately
tracks consumed training examples at `data/instructions/training_state.json`. Use `--replay`
to train previously consumed instruction examples again. `--reset` is not supported in
instruction mode. Normal training and instruction training should not run concurrently.

Each line of the reviewed JSONL contains exactly `tool`, `target`, `goal`, `args`,
`reason`, and `split` (`train`, `validation`, or `test`). Local targets and flags
are validated against the same deterministic planner policy. Validation/test records are
**never** used in instruction training. Examples too long for the checkpoint's byte
context are rejected; the script never truncates training intent. This is a small teaching
set, not a measured improvement in model quality. Keep functional benchmark fixtures
separate from instruction training examples and benchmark the resulting checkpoint.
The curriculum stages are validated in `config/instruction_train.json`; missing tool
categories fall back to a balanced sample of available categories. The small
reviewed example set is illustrative, and additional diverse reviewed examples
are needed before expecting reliable generation. Constrained JSON generation
forces an opening object and excludes control/non-ASCII bytes, but does not
guarantee valid JSON; strict schema and safety-policy validation still reject
invalid model responses.

## Model and dataset quality (offline-first)

Run a deterministic benchmark of the locally trained model's scanner-command proposals:

```bash
python src/model_quality.py --checkpoint checkpoints/latest.keras --output reports/model_quality.json
```

The benchmark uses reviewed cases in `config/model_quality_cases.json`. It scores JSON syntax, schema, policy validation, task arguments, explicit model refusals, and
policy-blocked unsafe proposals separately. An invalid proposal blocked by policy is **not** a model refusal.
It never executes scanners. Case goals/expected arguments are fixtures and do not prove that a
checkpoint is accurate in real network assessments. Keep benchmark examples out of training data.

For an offline smoke test without loading TensorFlow or a model, supply a JSON object mapping
every case ID to its generated response string:

```bash
python src/model_quality.py --responses /path/to/responses.json
```

Audit a prepared profile's source documents, persistent split hashes, manifest, and binary sizes
without changing or importing data:

```bash
python src/data_quality.py --corpus corpus/security --out data/security --report reports/data_quality_security.json
```

The audit reports exact/trailing-whitespace duplicates, short files, filtered secret-containing
files, and train/validation history conflicts. Data-quality warnings are distinct from integrity failures. The CLI also checks the shared registry and legacy profile histories for conflicting assignments. Invalid persisted split state fails closed.
This does **not** detect semantic duplicates or prove dataset licensing, and binary size
checks in the audit do not authenticate binary contents. Preserve split history and review audit results
before incremental/replay training; test datasets must remain excluded from training.

## Tests and further details

```bash
python -m unittest discover -s tests -v
```

Scanner unit tests do not require Nmap/Nuclei binaries. See the [technical reference](docs/REFERENCE.md) for configuration fields, import safeguards, checkpoint comparisons, stable document identities, scanner restrictions and migration notes.

