#!/usr/bin/env python3
"""Command-line entry point for Vorpal's scanner and knowledge workflows."""

import sys
from pathlib import Path


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "knowledge":
        # Existing project scripts use top-level imports from src/.
        source = str(Path(__file__).resolve().parent / "src")
        if source not in sys.path:
            sys.path.insert(0, source)
        from knowledge import main as knowledge_main
        return knowledge_main(sys.argv[2:])
    from src.local_vuln_scan import main as scan_main
    return scan_main()


if __name__ == "__main__":
    raise SystemExit(main())
