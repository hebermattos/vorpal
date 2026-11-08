"""Start Ollama on the loopback endpoint configured for Vorpal analysis."""

import argparse
import os
import shutil
import sys

try:
    from .config import ConfigError, load_config
except ImportError:
    from config import ConfigError, load_config


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Start Ollama locally using config/scan_intelligence.json. "
                    "Install Ollama and prepare a model separately."
    )
    parser.parse_args(argv)
    try:
        settings = load_config("scan_intelligence")
        executable = shutil.which("ollama")
        if executable is None:
            raise RuntimeError("Ollama is not installed or not on PATH. Install it from https://ollama.com/download.")
        host = f'127.0.0.1:{settings["port"]}'
        environment = dict(os.environ, OLLAMA_HOST=host)
        print(f"Starting Ollama at http://{host}; Vorpal model: {settings['model']}", flush=True)
        print(f"Manage models in another terminal with OLLAMA_HOST={host} ollama list", flush=True)
        print("Stop with Ctrl+C. Model downloads are separate from this launcher.", flush=True)
        # Replace the launcher so terminal signals reach Ollama directly.
        os.execve(executable, [executable, "serve"], environment)
    except (ConfigError, RuntimeError, OSError) as exc:
        parser.exit(2, f"Ollama startup failed: {exc}\n")


if __name__ == "__main__":
    sys.exit(main())
