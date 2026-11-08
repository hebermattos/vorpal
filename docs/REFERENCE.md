# Vorpal technical reference

Vorpal provides authorized local-IP evidence analysis and security-advice chat,
using one shared Ollama + RAG backend for CLI and UI. The primary walkthrough is
in the [README](../README.md).

## Runtime and dependencies

Python 3.11 on Linux/WSL is recommended. `requirements.txt` supplies corpus
extraction dependencies. Ollama is an independently managed external service;
its chosen model must already be available. Vorpal only sends inference
requests to the configured endpoint. It does not install, launch, stop, update
or configure Ollama, or download/copy Ollama models.

Hybrid retrieval optionally uses CPU PyTorch, Sentence Transformers and a
separately installed local embedding model. Source imports require
`--import-sources` and preserve incremental caches.

## Configuration

Every retained JSON config has a strict entry in `config/schema.json`.
`src/config.py` validates types, keys, ranges and relevant safety policies.
Files are located relative to the repository; command paths generally resolve
from the working directory, while the dashboard runs jobs from the project root.

| File | Purpose |
| --- | --- |
| `scan_intelligence.json` | Local inference host, port, model, generation/request limits and saved-report prompt |
| `assistant.json` | Chat/analysis prompts, default ports, collector choice, TCP/Nmap limits, bounded history and local logs |
| `rag.json` | Retrieval database, corpus, chunking, BM25/hybrid ranking and embedding directory |
| `knowledge.json` | Corpus indexing, import destination and audit reports |
| `corpus.json` | Eligible local extensions and excluded directories, filenames and internal metadata |
| `import_dataset.json` | Supported document types, extraction and download restrictions |
| `scraping.json` | Bounded same-origin crawling, robots checks and request pacing |
| `content_safety.json` | Heuristic secret-content patterns |
| `ingest_url.json` | Compatibility URL-import command defaults |
| `security_tools.json` | Tool planning prompt, argument policy, execution bounds and pinned templates |
| `web.json` | Loopback dashboard address, job limits, polling and logs |

Inference defaults to `http://127.0.0.1:11434`, model `qwen2.5-coder:3b`.
Only `127.0.0.1` and `localhost` inference hosts are accepted. HTTP proxies and
redirects are disabled. Ollama's OpenAI-compatible chat API is used with a JSON
response format. Requests and replies are bounded; malformed output and
unsupported source IDs fail closed.

## Assistant and evidence flow

`vorpal.py analyze --target IP --authorized` and the positional IP shortcut use
`src/assistant.py`. Authorization, target scope and selected ports are validated
before any target connection. A populated RAG index is required first.
`--tool tcp` performs bounded TCP connects and passive banner reads.
`--tool nmap` first generates a goal-based proposal through Ollama + RAG.
The planner receives the exact selected ports as constraints; deterministic
validation rejects expanded port scope or unsupported arguments. The command,
rationale, references, saved plan ID and digest are returned for human review.
A second request with `--plan-id`, `--digest` and `--authorized` executes that
reviewed command; target, ports, executable and policy fingerprints are checked
again before starting the externally installed Nmap executable. Service identification
sends active probes; scripts and arbitrary shell arguments remain forbidden.
The `default_tool`, `planning_goal`, deadline and output cap live in `assistant.json`.
`--goal` overrides the planning objective. `nmap_required_flags` sets minimum
evidence capabilities; `planning_attempts` caps correction requests to the
model. The planner receives the allowed argument catalog and previous rejection
reason. Invalid proposals have no command fallback. Returned
`execute_after_review` is a shell-quoted CLI request for the exact reviewed plan.
`src/collectors.py` bounds execution and combined stdout/stderr bytes, preserves
separate raw evidence files, and terminates the owned child on failure or
cancellation. The exact standard `<!DOCTYPE nmaprun>` declaration is removed before parsing;
other XML DTDs/entities remain rejected. Only matching-target, selected-port TCP
observations are retained. Port hints and tool-reported versions are provisional.
Closed/unreachable outcomes are not interpreted as proof of safety.

Collected observations and local reference excerpts are passed as untrusted data
to the model. Advice remains unverified. Reports retain observations even when
inference fails, under `logs/assistant/analysis_<id>.json`.

Assessments are isolated by port. `port_analyses` contains each selected port's
status, evidence, answer and sources. Each open port gets its own retrieval and
inference request with only that port's observations. Product/version/service
terms lead its retrieval query. Citation IDs are local to each port; aggregate
source entries carry a `port` field. Non-open/unreported ports are marked without
inference. A failed port does not discard successful results; mixed outcomes
have `partial` status. All-open-port failures raise an error after preserving
the report; no-open-port reports do not call the model. Each completed port is
persisted. `max_report_bytes` bounds reports separately from conversation size.

Chat uses the same inference/retrieval path, with bounded conversation turns.
A report ID binds follow-up chat to saved observations without another scan.
Sessions persist under `logs/assistant/chat_<id>.json`; the chosen RAG database
is retained per session. Sources include paths, lines and content hashes.
A source ID does not establish that a claim is supported by the excerpt.

`src/scan_intelligence.py` interprets existing Nmap XML/Nuclei JSONL separately.
It never invokes scanners. Regular-file/byte limits, exact-target filtering and
custom XML entity/DTD rejection apply; the inert standard Nmap declaration is accepted. Model-proposed next-check arguments pass the
same deterministic scanner policy as the separate tool planner.

## Knowledge and retrieval

`knowledge update` indexes existing local UTF-8 corpus text. `--import-sources`
opts into URL lists, local PDF extraction and bounded scraping. The importer
filters sensitive content and preserves incremental state. Successful URLs are
not automatically re-fetched; failures retry. Crawling remains in each seed's
origin and containing directory, following configured limits and robots rules.
Downloaded content is never executed. Internal metadata is excluded from RAG.

The default index is lexical BM25 with SQLite FTS5, or a portable fallback if
FTS5 is unavailable. Optional hybrid retrieval uses normalized local embeddings,
cosine similarity and reciprocal rank fusion. It does not modify chat-model
weights. Model/index mode changes require a new database. Keep the database
paths aligned in knowledge and retrieval configuration; different corpus roots
must not share one index. Knowledge audits are saved under `logs/knowledge/`.

## Dashboard API and jobs

`src/web.py` runs the loopback-only dashboard. `GET /api/state` returns the token,
configured inference endpoint/model, job history and available config files.
Mutations validate Host, Origin and `X-Vorpal-Token`, accept bounded JSON objects,
and reject unknown parameters.

- `POST /api/chat`: shared security chat using `message`, optional `session_id`
  and optional `report_id`.
- `POST /api/jobs`: allowlisted `analyze_ip`, `knowledge_update`, `tool_plan` or
  `tool_run` action and its validated parameters. No arbitrary shell action.
- `GET /api/jobs/ID` and `POST /api/jobs/ID/cancel`: inspect/cancel background jobs.
- `GET /api/config?name=NAME` and `POST /api/config`: inspect/save validated JSON.

Chat and jobs share admission control. Settings edits and development reloads
wait until requests finish. Job output is bounded in memory and saved in
`logs/web/`; cancellation interrupts the owned process group. Model text is
rendered through text APIs, never inserted as executable HTML.

## Scanner policy

`security_tools.py plan` uses local inference and required RAG, then validates
exactly `args` and `reason` before saving a proposal. It does not run scanners.
Execution requires an unchanged reviewed digest and explicit authorization.
Targets are single local IPv4 literals (Nuclei accepts a bounded HTTP origin).
Arguments, retries, rates and runtime are restricted. Nuclei templates and its
isolated config are hash-pinned; redirects, external callbacks, updates and
arbitrary scripts are disabled. Tool reports remain provisional observations.

## Validation

Run `python -m unittest discover -s tests -v` and `node --check web/app.js`. CI installs only the
runtime dependencies and runs the full offline suite. Real inference tests may
use a local model and an owned loopback fixture; never scan an arbitrary LAN
host for validation. If browser access is unavailable, distinguish HTTP/asset
checks from visual browser testing.
