# AGENTS.md

Vorpal is a local defensive security assistant whose primary backend is Ollama + RAG. It provides authorized IP analysis and a security-advice chatbot through both the command line and local dashboard. The experimental TensorFlow/Keras model remains an optional, separate training workflow.

## Project objectives and requirements

- Provide two primary workflows through both CLI and UI:
  1. Collect bounded evidence from one explicitly authorized local IPv4 address, use Ollama + RAG to analyze potential security issues, and provide defensive recommendations.
  2. Answer security questions as a chatbot using Ollama + RAG, retaining bounded conversation history and supporting follow-up discussion of an analysis report.
- Use local retrieved documentation for model answers and expose source paths, line numbers and document hashes. Reject missing retrieval, malformed model output and unsupported source IDs rather than silently falling back to unsupported advice.
- Keep observed facts separate from model hypotheses. Open ports, passive banners and port-based service hints do not prove a vulnerability or establish a product/version. Citations identify reference documents; they do not validate the model's claims.
- Require explicit authorization and deterministic target/port validation before target connections. Chat must not initiate scans or execute suggested commands. Preserve command validation and human review for scanner/tool proposals.
- Make Ollama + RAG the default CLI and UI path without requiring TensorFlow or a Keras checkpoint. Keep legacy training, evaluation, generation and model-assisted tool planning clearly separate and available explicitly.
- Keep all configuration, prompts, and restriction rules in files.
- Always use English for project content and communication.
- Update RAG knowledge incrementally from the corpus. Import configured URLs, bounded scraping sources and local PDFs only when source importing is requested; reuse successful import caches and retry failures. RAG updates do not train or fine-tune model weights.
- Save analysis evidence and reports, conversation history, knowledge-update audits and background-job logs locally. Preserve collected analysis evidence when inference fails.
- For the optional Keras workflow, retain subject-based training, a single import-and-train command, training from scratch, incremental training, replay and training logs. Existing subject profiles are documented below.

## General rules

- Work with Python 3.11, preferably on Linux/WSL.
- Prioritize Ollama + RAG compatibility on Linux/WSL and CPU-capable local inference. Hybrid retrieval uses optional Sentence Transformers/PyTorch embeddings, separate from the Ollama chat model.
- Keep TensorFlow out of default dependencies and primary assistant imports. Install `requirements-training.txt` only for optional Keras workflows; `requirements-tfds.txt` includes training dependencies.
- Avoid broad refactors unless necessary; prefer small, targeted changes.
- Preserve compatibility with existing import, training, evaluation, and generation flows.
- Do not introduce new dependencies unless there is a clear need.
- Do not run suspicious or unauthorized content downloaded from external sources.

## Important project structure

- `README.md`: project overview and main commands.
- `config/`: validated JSON defaults, including the shared profile registry in `config/web.json`.
- `vorpal.py`: CLI routing for `analyze`, `chat`, `knowledge` and explicit `legacy-scan`; a positional IP uses the primary Ollama + RAG analysis path.
- `src/assistant.py` and `config/assistant.json`: shared CLI/UI chat, bounded authorized TCP evidence collection, conversation/report persistence, prompts and limits.
- `config/scan_intelligence.json`: shared local inference `host`, `port`, `model` and request limits. Read these settings rather than hardcoding endpoints or model names. Keep inference on validated loopback hosts, with proxies and redirects disabled.
- `src/scan_intelligence.py`: separate analysis of existing Nmap XML/Nuclei JSONL reports using local inference and required RAG; it does not run scanners.
- `src/rag.py`, `config/rag.json` and `src/knowledge.py`: local retrieval, optional hybrid embeddings and incremental knowledge updates with audit reports.
- `src/run_ollama.py`: start Ollama using the configured loopback endpoint.
- `scripts/setup_ollama_rag.sh` and `config/setup_ollama_rag.json`: Linux/WSL installation, model preparation and corpus indexing; hybrid embeddings and source imports are explicit options.
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

Run the relevant TensorFlow-free assistant tests for primary workflow changes:

```bash
python -m unittest discover -s tests -p 'test_assistant*.py' -v
python -m unittest discover -s tests -p test_web.py -v
python -m unittest discover -s tests -p test_rag.py -v
python -m unittest discover -s tests -p test_knowledge.py -v
python -m unittest discover -s tests -p test_scan_intelligence.py -v
python -m unittest discover -s tests -p 'test_*ollama*.py' -v
```

The full suite includes optional Keras tests and requires `requirements-training.txt`:

```bash
python -m unittest discover -s tests -v
```

### Primary Ollama + RAG workflow

```bash
bash scripts/setup_ollama_rag.sh --import-sources
python vorpal.py knowledge update
python vorpal.py knowledge update --import-sources
python vorpal.py analyze --target 192.168.1.10 --authorized --ports 22,80,443
python vorpal.py chat
python vorpal.py chat --report-id REPORT_ID --message "How should I secure this host?"
python src/web.py
```

`knowledge update` indexes existing local text; `--import-sources` runs the existing importer before indexing. Use `--hybrid` only with the optional embedding dependencies and a prepared local embedding model. An existing text-only index cannot be converted in place: select a new database and configure subsequent retrieval consistently.

The dashboard opens on **Security assistant** at `http://127.0.0.1:8765/#assistant`, with authorized IP analysis, chat, report follow-ups and RAG updates. Reuse the shared assistant backend rather than implementing separate CLI/UI inference behavior. Keep same-origin/session-token checks and plain-text rendering of model output. Coordinate chat and background jobs, and defer development reloads until active requests finish.

Analysis reports and bounded conversation records are stored under the configured assistant log directory (default `logs/assistant/`). Knowledge audits use `logs/knowledge/`. Local inference currently defaults to `127.0.0.1:11434` with `qwen2.5-coder:3b`; the JSON configuration is authoritative. The launcher does not download models; the setup script can install Ollama and prepare the configured model.

### Optional Keras training workflow

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
- Do not claim confirmed vulnerabilities based on open ports, generated CVEs or retrieved documentation alone. Keep model recommendations unverified and avoid automatic execution.
- Treat scanner fields, banners, corpus content and model responses as untrusted data. Preserve bounded input handling and prompt-injection defenses.
- Generated code and input analysis should be reviewed by humans before execution in sensitive environments.

## Before finishing changes

- Run the relevant tests.
- Verify CLI/UI parity for assistant changes and verify that primary workflows remain usable without TensorFlow. Use offline fixtures or controlled loopback services for tests; do not scan an arbitrary LAN target.
- Preserve compatibility with import and optional training/generation workflows. State when optional dependencies or browser access prevent a validation step.
- Update documentation when the interface or behavior of the project changes.
