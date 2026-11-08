# Vorpal

Vorpal is a local defensive security assistant using **Ollama + RAG**. It has two
primary workflows, available through both the CLI and a local dashboard:

1. Observe selected TCP ports on one authorized local IP, analyze the evidence
   with local reference documents, and provide unverified security advice.
2. Answer security questions as a chatbot, with bounded conversation history,
   documentation sources, and follow-up discussion of an IP report.

Open ports, banners and documentation citations do not establish a confirmed
vulnerability. Review model advice and commands independently.

## Install

Use Python 3.11 on Linux/WSL. Install Vorpal's Python dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

**Ollama is an external dependency.** Run and manage it independently, with the
chosen model already available. Vorpal does not install, start, stop, update or
configure the Ollama service, and does not download or copy its models.

## Configure Ollama

`config/scan_intelligence.json` is the shared CLI/UI inference configuration:

```json
{
  "host": "127.0.0.1",
  "port": 11434,
  "model": "qwen2.5-coder:3b"
}
```

These are fields in the existing file; keep its other settings. Only validated
loopback hosts are accepted. Proxy use and redirects are disabled. Use an
installed model name provided by the independently managed Ollama service.

Restart long-running commands after configuration changes. The dashboard
validates JSON edits through **Settings**. A compatible local OpenAI API server
can also be used when its model name and endpoint match the configuration.

## Update knowledge

```bash
# Index existing local text:
python vorpal.py knowledge update

# Import URLs, scraping sources and PDFs before indexing:
python vorpal.py knowledge update --import-sources
```

Knowledge updates synchronize the configured corpus incrementally. They do not
change Ollama model weights. Successful source imports are cached; failed
sources retry. Previously accepted URLs are not automatically re-fetched.
Removed local documents are removed from the index. Audit reports are saved
under `logs/knowledge/`.

Defaults are `corpus/` and `data/rag/index.sqlite3`, configured in
`config/knowledge.json` and `config/rag.json`. Keep their database paths aligned.
Do not reuse one index for different corpus roots. Scanner fields, local files
and retrieved documents remain untrusted; content filters are heuristic.

Files named `url` list sources for the existing importer. Files named `scraping`
list public HTTP(S) starting URLs. Crawling stays within each seed's origin and
containing directory, obeying robots checks, depth/page/byte limits and pacing
from `config/scraping.json`. Preserve import state and `scraping_state.json`;
they are metadata excluded from retrieval. Local PDFs are extracted without OCR.
Rejected secrets or unsupported content require review outside the importer.

### Optional hybrid retrieval

Text-only BM25 retrieval requires no embedding model. Optional hybrid retrieval
uses CPU embeddings and reciprocal rank fusion. Install the optional embedding
dependencies separately and provide a compatible local Sentence Transformers
model directory in `config/rag.json` under `embedding_model`:

```bash
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install sentence-transformers
python vorpal.py knowledge update --hybrid --import-sources
```

The embedding model is separate from the external Ollama model and must already
be installed locally; indexing does not download it. A text index cannot be
switched in place to hybrid mode. Use `--database data/rag/hybrid.sqlite3` for a
new index, then pass `--rag-db data/rag/hybrid.sqlite3` during analysis/chat or set
`database` consistently in the knowledge and retrieval configs. Do not run
concurrent index writers.

## Analyze an authorized IP

```bash
python vorpal.py analyze --target 192.168.1.10 --authorized --tool nmap --ports 22,80,443
# Equivalent positional shortcut:
python vorpal.py 192.168.1.10 --authorized --ports 22,80,443
```

Select the evidence collector with `--tool`:

- `nmap`: run the separately installed Nmap executable with policy-validated,
  unprivileged TCP/service-identification arguments. Only the selected local
  target and ports are included; version identification sends active probes.
- `tcp`: use built-in TCP connections and passive banner reads, with no external
  scanner. This remains the configured default.

The dashboard exposes the same **Evidence tool** selector. Set `default_tool`
in `config/assistant.json` to `nmap` to use it when the parameter is omitted.
Nmap arguments, execution timeout and output limits are also stored there;
`config/security_tools.json` enforces the shared scanner policy. Arbitrary tools,
shell commands, Nmap scripts and unvalidated arguments are not accepted. Vorpal
does not install Nmap or silently switch collectors if it is unavailable.

Authorization and one private, loopback or link-local IPv4 literal are required.
There is no subnet scan. Default ports and TCP connection limits live in
`config/assistant.json`. Port-based service names are only hints; Nmap service
and version fields are provisional observations. For built-in TCP collection,
connection failures are grouped as closed or unreachable. Neither a failed
connection nor a service/version match proves that a host is secure or vulnerable.

Ollama analyzes observations using RAG references. Advice is unverified and no
suggested command is automatically executed. Reports, including evidence when
inference fails, are saved to `logs/assistant/analysis_<report-id>.json`. Use
`--output reports/analysis.json` for an additional copy. Nmap collection also
saves raw `.nmap.xml` and `.nmap.stderr.log` evidence beside the JSON report,
including bounded partial output when collection fails.

Analysis is **per port**: `port_analyses` contains an entry for every selected
port. Each observed open port receives a separate RAG lookup and Ollama request,
with only that port's evidence, advice and source list. Citation IDs are scoped
to each port; the top-level source list includes a `port` field. Ports not
observed open are recorded without an AI assessment or a claim of safety.
The UI displays these assessments separately, including reported product/version
fields when available. Results are saved after each assessment; one port's
failure produces a `partial` report if other assessments succeed. If all open
ports fail, the command reports an error and preserves their recorded errors.
No open ports means no inference request. Requests run sequentially, so restrict
`--ports` when desired to control total inference time.

For richer existing scanner evidence, a separate saved-report analyzer remains:

```bash
python src/scan_intelligence.py --target 192.168.1.10 --authorized \
  --nmap-xml /path/to/existing/report.xml --output reports/analysis.json
```

It also accepts `--nuclei-jsonl`. It does not run scanners. Inputs must be regular
files within configured size limits; custom DTDs and entities are rejected; the standard inert Nmap declaration is accepted.

## Security chatbot

```bash
python vorpal.py chat
python vorpal.py chat --message "How can I secure SSH access?"
python vorpal.py chat --session-id SESSION_ID --message "What about SSH keys?"
python vorpal.py chat --report-id REPORT_ID --message "How should I secure this host?"
```

Interactive chat ends with `/exit` or Ctrl+C. Responses contain documentation
source IDs, paths, lines and SHA-256 hashes. These citations identify documents;
they do not prove the model's claims. Missing retrieval, invalid output or an
unavailable model produces an error instead of unsupported fallback advice.
Conversation history is bounded and saved under `logs/assistant/`. Discussing a
report does not reconnect to its target.

## Dashboard

```bash
python src/web.py
```

Open **http://127.0.0.1:8765/#assistant**. The dashboard provides authorized IP
analysis, security chat, **Discuss this report in chat**, knowledge updates,
validated JSON settings, activity logs and cancellation. Analysis is a background
job; chat and jobs do not run simultaneously within one dashboard instance.
New chat or page reload resets the displayed conversation; saved session IDs
remain available to the CLI.

The server binds to loopback and requires same-origin/session-token protections
for mutations. Model text is rendered as text. Use `--port 8766` for another
port or `--reload` during development. Backend reload waits for jobs/chat to
finish. Unsaved input may be lost on refresh. Stop with Ctrl+C. Background-job
logs are saved under `logs/web/`.

## Reviewed tool commands

The separate Nmap/Nuclei planner also uses Ollama + RAG:

```bash
python src/security_tools.py plan --tool nmap --target 192.168.1.10 \
  --goal "Inspect SSH and web ports"
```

Planning never starts a scanner. Review the literal command and then use its
printed `run` command with the plan ID, digest and `--authorized`. Deterministic
validation binds the target and enforces bounded options, isolated execution and
reviewed, fingerprinted local Nuclei templates. No arbitrary scripts, downloaded
templates or model-supplied shell commands are executed. Reports are saved under
`reports/security_tools/`. Nmap/Nuclei executables are installed separately.

## Tests and reference

```bash
python -m unittest discover -s tests -v
node --check web/app.js
```

Tests use offline documents, mocked inference and controlled local fixtures;
they require no running Ollama server or scanner executables. See
[the technical reference](docs/REFERENCE.md) and [project instructions](AGENTS.md).
