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
For Llama, set `model` in `config/scan_intelligence.json` to the exact tag of
the Llama Instruct model already available in your Ollama instance.
Only `127.0.0.1` and `localhost` inference hosts are accepted. HTTP proxies and
redirects are disabled. Ollama's OpenAI-compatible chat API is used with a JSON
response format. Tool planning uses a generated JSON schema, with an enum of
actual allowed option tokens for application-scoped Nmap requests. This uses
Ollama's [structured output support](https://docs.ollama.com/capabilities/structured-outputs);
no lifecycle or model-download request is made. Servers must support this
response format; there is no silent downgrade of planning constraints.
Requests and replies are bounded; malformed output and
unsupported source IDs fail closed.

## Assistant and evidence flow

`vorpal.py analyze --target IP --authorized` and the positional IP shortcut use
`src/assistant.py`. Authorization, target scope and selected ports are validated
before any target connection. A populated RAG index is required first.
Live analysis returns cited advice per observed open port in `port_analyses`.
Use the [saved-report analyzer](#saved-report-analysis) when you need structured
hypotheses and next checks from an existing scanner export.
`--tool tcp` performs bounded TCP connects and passive banner reads.
`--tool nmap` first generates a goal-based proposal through Ollama + RAG.
The planner receives the exact selected ports as constraints. The application
attaches `-p` when the model omits it, using the requested or configured default
ports. An explicit different list, conflicting discovery modes, or unsupported
arguments are rejected; model-generated probe choices remain subject to the
capability checks. The command,
rationale, references, saved plan ID and digest are returned for human review.
A second request with `--plan-id`, `--digest` and `--authorized` executes that
reviewed command; target, ports, executable and policy fingerprints are checked
again before starting the externally installed Nmap executable. Service identification
sends active probes; scripts and arbitrary shell arguments remain forbidden.
The shipped `default_tool` is `tcp`. Set it to `nmap` to use reviewed Nmap
collection when the collector is omitted. The `planning_goal`, deadline, and
output cap also live in `assistant.json`.
`--goal` overrides the planning objective. `nmap_required_flags` sets minimum
evidence capabilities. `planning_attempts` caps the total model attempts
(two by default). The planner receives the allowed argument catalog and previous rejection
reason. Rejected attempts save their raw model responses and errors in a planning
diagnostic under `reports/security_tools/plans/`. The final error includes its
path. Invalid proposals have no command fallback. The returned
`execute_after_review` is a shell-quoted CLI request for the reviewed command,
target, ports, digest, plan ID, and RAG database.
`src/collectors.py` bounds execution and combined stdout/stderr bytes, preserves
separate raw evidence files, and terminates the owned child on failure or
cancellation. The exact standard `<!DOCTYPE nmaprun>` declaration is removed before parsing;
other XML DTDs/entities remain rejected. Only matching-target, selected-port TCP
observations are retained. Port hints and tool-reported versions are provisional.
Closed/unreachable outcomes are not interpreted as proof of safety.

Collected observations and local reference excerpts are passed as untrusted data
to the model. Advice remains unverified. Reports retain observations even when
inference fails, under `logs/assistant/analysis_<id>.json`. Use `--output` for an
additional report copy. Nmap evidence is saved next to the report as
`.nmap.xml` and `.nmap.stderr.log`, including bounded partial output on failure.

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
Use `chat --session-id SESSION_ID --message "What about SSH keys?"` to continue
a saved session. In interactive chat, use `/exit` or Ctrl+C to stop.

### Saved-report analysis

`src/scan_intelligence.py` reads Nmap XML (`--nmap-xml`), Nuclei JSONL
(`--nuclei-jsonl`), or both for the authorized target. It returns
`hypotheses` marked unverified and `next_checks` marked for review. It never
invokes scanners. Regular-file limits, exact-target filtering, and custom XML
entity/DTD rejection apply. The inert standard Nmap declaration is accepted.
Next-check arguments pass the same deterministic scanner policy as the separate
tool planner.

## Knowledge and retrieval

`knowledge update` indexes local UTF-8 text incrementally. Deleted documents
are removed from the index. The defaults are `corpus/` and
`data/rag/index.sqlite3`. Keep `database` aligned in `config/knowledge.json` and
`config/rag.json`. Use a separate index for each corpus root, and run one index
writer at a time. Audit reports are saved under `logs/knowledge/`.

File-read or metadata errors retain the last successfully indexed hash, chunks
and vectors for that source; a subsequent update retries it. Other readable
documents still update. Retained content may be stale. Invalid UTF-8, oversized,
too-short, secret-bearing or policy-excluded content is rejected and its previous
index entries are removed. A directory-enumeration failure cancels and rolls back
the synchronization because an incomplete inventory cannot establish deletion.
Knowledge audits include `indexing.retained`, `deleted`, `rejected` (with reasons)
and `failed` (with errors). The existing `changes.removed` includes both deleted
and rejected documents that previously existed in the index.

Before returning references, retrieval applies `relevance` in `config/rag.json`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `min_lexical_coverage` | `0.2` | Minimum fraction of distinct query terms found in a chunk, after configured stop words are removed |
| `min_semantic_similarity` | `0.55` | Alternative minimum cosine similarity, available only in hybrid mode |
| `stop_words` | English/Portuguese function words | Terms ignored for lexical matching and coverage |

Both thresholds must be greater than zero and no greater than one. Coverage uses
the first 32 query tokens, excluding configured stop words. A chunk must satisfy
at least one threshold; rank-fusion scores alone never qualify it. Queries with
no remaining terms return no references. Automatic per-port analysis retrieves
using observed port/service/product/version fields rather than its formatting
prompt. If no reference qualifies, existing CLI/UI chat, saved/live analysis and
tool-planning error paths stop before inference; collected evidence is retained.
These defaults are conservative heuristics tested with synthetic fixtures, not
calibrated confidence probabilities. Tune them against reviewed queries for the
chosen corpus and embedding model. Passing the gate does not verify a claim,
service identification or vulnerability; semantic false matches and lexical
coincidences remain possible.

`--import-sources` opts into source imports before indexing. Files named `url`
list direct sources. Files named `scraping` list public HTTP(S) crawl seeds.
Crawling stays within each seed's origin and containing directory, using the
robots checks, pacing, and page, depth, and byte limits in `config/scraping.json`.
Local PDFs are extracted without OCR. Successful imports are cached. Accepted
URLs are not automatically re-fetched, and failed sources retry. Preserve the
import state and `scraping_state.json`, which are excluded from retrieval.
Downloaded content is never executed. Treat corpus content, retrieved excerpts,
and scanner fields as untrusted. The content filters are heuristic.

### Optional hybrid retrieval

The default text index uses BM25 with SQLite FTS5 and has a portable fallback.
Hybrid retrieval combines text results with cosine similarity over normalized
local embeddings using reciprocal rank fusion. Install CPU embedding
dependencies separately and set `embedding_model` in `config/rag.json` to a
compatible local Sentence Transformers model directory. This model is separate
from the model served by Ollama.

```bash
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install sentence-transformers
python vorpal.py knowledge update --hybrid --database data/rag/hybrid.sqlite3
python vorpal.py chat --rag-db data/rag/hybrid.sqlite3 --message "How can I secure SSH access?"
```

Add `--import-sources` to the update command if you also want to import source
lists and PDFs. Use `--rag-db data/rag/hybrid.sqlite3` for CLI analysis or chat.
To use the hybrid index by default, including in the dashboard, set `database`
in both knowledge and retrieval configs to that path. Use a new database if
switching retrieval modes or embedding models.

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
wait until requests finish. Use `--port 8766` for another port and `--reload` for
development. New chat or page reload resets the displayed conversation. Saved
session IDs remain available from the CLI. Job output is bounded in memory and
saved in `logs/web/`. Cancellation interrupts the owned process group. Model
output is rendered as text.

## Scanner policy

The separate Nmap/Nuclei planner uses local inference and RAG, then validates
`args` and `reason` before saving a proposal under `reports/security_tools/`.
The scanner executables are installed separately.

```bash
python src/security_tools.py plan --tool nmap --target 192.168.1.10 \
  --goal "Inspect SSH and web ports"
```

Planning prints a separate `run` command for review and starts no scanner.
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
