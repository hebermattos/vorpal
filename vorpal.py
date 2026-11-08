#!/usr/bin/env python3
"""Command-line entry point for Vorpal's scanner and knowledge workflows."""

import sys
import argparse
from pathlib import Path


def main():
    if len(sys.argv) >= 2 and sys.argv[1] in {'chat', 'analyze'}:
        source = str(Path(__file__).resolve().parent / 'src')
        if source not in sys.path:
            sys.path.insert(0, source)
        from assistant import main as assistant_main
        return assistant_main(sys.argv[1:])
    if len(sys.argv) >= 2 and sys.argv[1] == "knowledge":
        # Existing project scripts use top-level imports from src/.
        source = str(Path(__file__).resolve().parent / "src")
        if source not in sys.path:
            sys.path.insert(0, source)
        from knowledge import main as knowledge_main
        return knowledge_main(sys.argv[2:])
    if len(sys.argv) >= 2 and not sys.argv[1].startswith('-'):
        source = str(Path(__file__).resolve().parent / 'src')
        if source not in sys.path:
            sys.path.insert(0, source)
        from assistant import main as assistant_main
        return assistant_main(['analyze', '--target', *sys.argv[1:]])
    parser = argparse.ArgumentParser(description='Vorpal: local Ollama + RAG security assistant')
    parser.add_argument('command', choices=['analyze', 'chat', 'knowledge'], nargs='?',
                        help='Use COMMAND --help for options.')
    parser.parse_args()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
