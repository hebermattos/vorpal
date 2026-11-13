# Vorpal

Vorpal is a local defensive security assistant using **Ollama + RAG**. Use it to
ask security questions with citations from local documents or analyze evidence
from one authorized local IPv4 address. Review model advice and commands
independently. Open ports, banners, and citations alone cannot confirm a
vulnerability.

## Quick start

Use Python 3.11 on Linux/WSL and run the examples from the repository root.

### 1. Install Vorpal

```bash
git clone https://github.com/hebermattos/vorpal.git
cd vorpal
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### 2. Configure Ollama

Run Ollama independently with the chosen model already installed. In
[`config/scan_intelligence.json`](config/scan_intelligence.json), set `host`,
`port`, and `model`, leaving the other settings in place. The defaults are
`127.0.0.1`, `11434`, and `qwen2.5-coder:3b`.

To use Llama, set `model` to the exact tag of your installed **Llama Instruct**
model. The local endpoint must support JSON responses and schema-constrained
structured output for tool planning. Restart long-running Vorpal processes after
editing the config. See [inference configuration](docs/REFERENCE.md#configuration).

### 3. Index local documents and ask a question

```bash
python vorpal.py knowledge update
python vorpal.py chat --message "How can I secure SSH access?"
```

The default text index uses the bundled `corpus/`. Add `--import-sources` to
process URL lists, bounded scraping sources, and local PDFs before indexing.
Knowledge updates leave the model weights intact. See
[source imports](docs/REFERENCE.md#knowledge-and-retrieval) and
[optional hybrid retrieval](docs/REFERENCE.md#optional-hybrid-retrieval).

Retrieval applies configurable minimum relevance checks. When no reference
qualifies, chat, analysis and planning stop before calling the model. These
checks are heuristics and do not establish that a reference supports a claim.
Index updates preserve previously indexed content after temporary file-read
failures and record those failures for retry on the next update.

For the dashboard, run `python src/web.py` and open
[Security assistant](http://127.0.0.1:8765/#assistant). Use `python vorpal.py chat`
for an interactive terminal session.

## Analyze an authorized IP

Use a single private, loopback, or link-local IPv4 address you are authorized
to assess. The built-in `tcp` collector connects to selected ports and reads
passive banners. It is the configured default. Use `nmap` for service probes
with a separate command review step. Install the Nmap executable independently.

```bash
python vorpal.py analyze --target 192.168.1.10 --authorized --tool nmap \
  --ports 22,80,443 --goal "Identify SSH and web service versions with low-impact probes"
```

This request returns a command for review and starts no scan. Review the
literal command and target, then run the returned `execute_after_review`
command, which includes the plan ID, digest, and authorization flag. In the
dashboard, use **Generate Nmap command for review**, confirm the exact command
and target, then select **Run reviewed command and analyze ports**.

Live analysis gives cited advice for each observed open port and saves
evidence under `logs/assistant/`. Follow up on a saved report without
reconnecting to the target.

```bash
python vorpal.py chat --report-id REPORT_ID --message "What should I verify next?"
```

## Analyze an existing scan report

Use the saved-report analyzer for structured `hypotheses` and `next_checks`.
It reads existing Nmap XML or Nuclei JSONL and marks proposed checks for human
review.

```bash
python src/scan_intelligence.py --target 192.168.1.10 --authorized \
  --nmap-xml /path/to/existing/report.xml --output reports/analysis.json
```

See the [analysis reference](docs/REFERENCE.md#assistant-and-evidence-flow) for
report fields, partial results, and the [separate Nmap/Nuclei planner](docs/REFERENCE.md#scanner-policy).

## Validation and reference

```bash
python -m unittest discover -s tests -v
node --check web/app.js
```

The offline suite uses mocked inference and controlled local fixtures.
See the [technical reference](docs/REFERENCE.md) and [project instructions](AGENTS.md).
