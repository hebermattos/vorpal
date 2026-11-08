#!/usr/bin/env bash
# Install local inference prerequisites and index the configured Vorpal corpus.
set -Eeuo pipefail

usage() {
    cat <<'EOF'
Usage: bash scripts/setup_ollama_rag.sh [--hybrid] [--import-sources] [--database PATH]

Install Ollama if missing, prepare the configured local model, and index the corpus.
--hybrid         Install CPU embedding dependencies and download the embedding model.
--import-sources Import configured URLs/PDFs before indexing (accesses public websites).
--database PATH  Use a separate RAG index, for example when switching to hybrid mode.
Defaults: config/setup_ollama_rag.json, config/scan_intelligence.json,
          config/knowledge.json and config/rag.json.
Downloads packages/models; the official Ollama installer may request sudo on Linux/WSL.
EOF
}

hybrid=false
knowledge_args=()
while (($#)); do
    case "$1" in
        --hybrid) hybrid=true; knowledge_args+=(--hybrid); shift ;;
        --import-sources) knowledge_args+=(--import-sources); shift ;;
        --database)
            [[ $# -ge 2 && -n "$2" ]] || { echo 'Missing --database value.' >&2; exit 2; }
            knowledge_args+=(--database "$2"); shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
mkdir -p logs/setup
stamp=$(date +%Y%m%d_%H%M%S)_$$
log="logs/setup/setup_${stamp}.log"
exec > >(tee -a "$log") 2>&1
echo "Setup log: $log"
server_pid=''
cleanup() {
    status=$?
    if ((status != 0)); then
        echo "Setup failed (exit $status). See $log. Existing indexes and checkpoints are preserved."
        if [[ -n "$server_pid" ]]; then kill "$server_pid" 2>/dev/null || true; fi
    fi
}
trap cleanup EXIT

if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv .venv
fi
python="$PWD/.venv/bin/python"

# Load validated settings without evaluating configuration as shell code.
settings=$("$python" - <<'PY'
import sys
sys.path.insert(0, 'src')
from config import load_config
setup = load_config('setup_ollama_rag')
scan = load_config('scan_intelligence')
values = [setup['ollama_model'], scan['model'], str(scan['port']),
          str(setup['startup_timeout_seconds'])]
if any('\n' in value or '\r' in value for value in values):
    raise ValueError('Setup values must be single-line strings.')
if any(value.startswith('-') or 'cloud' in value.lower() for value in values[:2]):
    raise ValueError('Use a local Ollama model name, not an option or cloud model.')
print('\n'.join(values))
PY
)
mapfile -t values <<< "$settings"
model=${values[0]}
alias=${values[1]}
export OLLAMA_HOST="127.0.0.1:${values[2]}"
timeout_seconds=${values[3]}

"$python" -m pip install -r requirements.txt
if ! command -v ollama >/dev/null 2>&1; then
    [[ $(uname -s) == Linux ]] || { echo 'Install Ollama from https://ollama.com/download, then rerun.'; exit 1; }
    installer="logs/setup/ollama_install_${stamp}.sh"
    curl --fail --silent --show-error --location --proto '=https' \
        https://ollama.com/install.sh --output "$installer"
    sh "$installer"
fi
command -v ollama >/dev/null 2>&1 || { echo 'Ollama is still missing from PATH.'; exit 1; }

ready() {
    "$python" - <<'PY'
import json, os, urllib.request
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None
try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open('http://' + os.environ['OLLAMA_HOST'] + '/api/version', timeout=2) as response:
        value = json.loads(response.read(4096))
    assert isinstance(value.get('version'), str)
except Exception:
    raise SystemExit(1)
PY
}

if ! ready; then
    server_log="logs/setup/ollama_${stamp}.log"
    nohup ollama serve > "$server_log" 2>&1 < /dev/null &
    server_pid=$!
    deadline=$((SECONDS + timeout_seconds))
    until ready; do
        kill -0 "$server_pid" 2>/dev/null || { cat "$server_log"; exit 1; }
        ((SECONDS < deadline)) || { echo "Ollama startup timed out. See $server_log"; exit 1; }
        sleep 1
    done
    echo "Ollama started: PID $server_pid; server log: $server_log"
else
    echo "Reusing Ollama at $OLLAMA_HOST"
fi

# Reuse installed models instead of checking for new weights on every setup run.
if ! ollama show "$model" >/dev/null 2>&1; then ollama pull "$model"; fi
if [[ "$alias" != "$model" ]]; then
    if ! ollama show "$alias" >/dev/null 2>&1; then
        ollama cp "$model" "$alias"
    else
        echo "Preserving existing model alias: $alias"
    fi
fi

if $hybrid; then
    "$python" -m pip install torch --index-url https://download.pytorch.org/whl/cpu
    "$python" -m pip install sentence-transformers
    "$python" - <<'PY'
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, 'src')
from config import load_config
from sentence_transformers import SentenceTransformer
destination = Path(load_config('rag')['embedding_model']).resolve()
if destination.exists():
    SentenceTransformer(str(destination), device='cpu', local_files_only=True,
                        trust_remote_code=False, model_kwargs={'use_safetensors': True})
    print('Verified existing embedding model:', destination)
else:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        model = SentenceTransformer(load_config('setup_ollama_rag')['embedding_model_id'],
                                    device='cpu', trust_remote_code=False,
                                    model_kwargs={'use_safetensors': True})
        staged = Path(temporary) / 'model'
        model.save_pretrained(str(staged), safe_serialization=True)
        os.rename(staged, destination)
    print('Installed embedding model:', destination)
PY
fi

"$python" vorpal.py knowledge update "${knowledge_args[@]}"
echo "Setup completed. Ollama endpoint: http://$OLLAMA_HOST; Vorpal model: $alias"
if [[ -n "$server_pid" ]]; then echo "Stop this Ollama instance with: kill $server_pid"; fi
