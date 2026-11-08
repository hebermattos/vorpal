"""Incremental corpus import and RAG lifecycle management.

Does not run a scanner or modify local chat model weights. External imports occur only when explicitly requested.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

try:
    from .config import load_config
    from . import rag
    from .import_dataset import import_directory, ExtractError
except ImportError:
    from config import load_config
    import rag
    from import_dataset import import_directory, ExtractError

SETTINGS = load_config("knowledge")
ROOT = Path(__file__).resolve().parents[1]


class KnowledgeError(ValueError):
    pass


def _within_project(value, *, must_exist=False):
    """Resolve paths against the repository, rejecting traversals and symlinks."""
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT):
        raise KnowledgeError("Knowledge paths must remain inside the project.")
    if must_exist and not path.is_dir():
        raise KnowledgeError(f"Corpus directory does not exist: {path}")
    return path


def _snapshot(database):
    """Read the RAG index without creating or modifying it."""
    if not database.is_file():
        return {}
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as db:
        return dict(db.execute("SELECT source,sha256 FROM documents"))


def _verify(database, expected, settings):
    """Check indexed content and hashes, not relevance or model correctness."""
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as db:
        indexed = dict(db.execute("SELECT source,sha256 FROM documents"))
        count = db.execute("SELECT count(*) FROM chunks").fetchone()[0]
        row = db.execute("SELECT value FROM metadata WHERE key='root'").fetchone()
        if row is None or row[0] != str(expected):
            raise KnowledgeError("RAG index root does not match the selected corpus.")
        if not indexed and count:
            raise KnowledgeError("RAG contains chunks without indexed documents.")
        missing = db.execute(
            "SELECT count(*) FROM chunks c LEFT JOIN documents d ON d.source=c.source "
            "WHERE d.source IS NULL").fetchone()[0]
        if missing:
            raise KnowledgeError("RAG contains orphan document chunks.")
        # Keep this a structural integrity check; no ground-truth labels exist.
        integrity = db.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            raise KnowledgeError(f"SQLite integrity check failed: {integrity}")
        return {"documents": len(indexed), "chunks": count,
                "sqlite_integrity": integrity, "retrieval_quality_tested": False}


def update(*, corpus=None, database=None, import_sources=False, hybrid=None,
           settings=None, report_dir=None):
    settings = settings or SETTINGS
    root = _within_project(corpus or settings["corpus"], must_exist=True)
    dbpath = _within_project(database or settings["database"])
    logs = _within_project(report_dir or settings["report_dir"])
    if dbpath == root or dbpath.is_relative_to(root):
        raise KnowledgeError("RAG database must be outside the corpus to avoid indexing metadata.")
    if logs == root or logs.is_relative_to(root):
        raise KnowledgeError("Knowledge reports must be outside the corpus.")
    if dbpath.is_symlink() or logs.is_symlink():
        raise KnowledgeError("Knowledge output paths must not be symbolic links.")

    imported = {"accepted": 0, "rejected": 0}
    before = _snapshot(dbpath)
    if dbpath.is_file():
        with sqlite3.connect(f"file:{dbpath.as_posix()}?mode=ro", uri=True) as current:
            previous_root = current.execute(
                "SELECT value FROM metadata WHERE key='root'"
            ).fetchone()
        if previous_root and previous_root[0] != str(root):
            raise KnowledgeError(
                "Existing RAG index belongs to another corpus; choose a different database."
            )
    if import_sources:
        destination = root / settings["imported_subdir"]
        if not destination.resolve().is_relative_to(root) or destination.is_symlink():
            raise KnowledgeError("Imported document path must remain within the corpus.")
        accepted, rejected = import_directory(root, destination, force=False)
        imported = {"accepted": len(accepted), "rejected": len(rejected)}
    if hybrid is None:
        # Preserve an existing index mode. New indexes use text-only by default.
        if dbpath.exists():
            with sqlite3.connect(f"file:{dbpath.as_posix()}?mode=ro", uri=True) as db:
                try:
                    mode = db.execute("SELECT value FROM metadata WHERE key='encoder'").fetchone()
                except sqlite3.OperationalError:
                    mode = None
            hybrid = bool(mode and mode[0])
        else:
            hybrid = False
    stats = rag.index(root, dbpath, hybrid=hybrid)
    after = _snapshot(dbpath)
    added = sorted(after.keys() - before.keys())
    removed = sorted(before.keys() - after.keys())
    changed = sorted(k for k in before.keys() & after.keys() if before[k] != after[k])
    verification = _verify(dbpath, root, settings)
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus": str(root.relative_to(ROOT)),
        "database": str(dbpath.relative_to(ROOT)),
        "mode": stats["mode"],
        "import": imported,
        "changes": {"added": added, "updated": changed, "removed": removed},
        "index": verification,
        "scan_started": False,
    }
    logs.mkdir(parents=True, exist_ok=True)
    # Content-addressed snapshot of the report, with unique run timestamp.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = logs / f"update_{stamp}.json"
    destination.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    result["report"] = str(destination.relative_to(ROOT))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Manage incremental local RAG knowledge")
    action = parser.add_subparsers(dest="action", required=True)
    command = action.add_parser("update", help="Synchronize imported documents with RAG")
    command.add_argument("--corpus", default=SETTINGS["corpus"])
    command.add_argument("--database", default=SETTINGS["database"])
    command.add_argument("--import-sources", action="store_true",
                         help="Opt in to importer source lists (may fetch public URLs)")
    command.add_argument("--hybrid", action="store_true",
                         help="Require locally installed embeddings for this index")
    args = parser.parse_args(argv)
    try:
        result = update(corpus=args.corpus, database=args.database,
                        import_sources=args.import_sources,
                        hybrid=True if args.hybrid else None)
    except (KnowledgeError, ExtractError, ValueError, OSError, sqlite3.Error,
            RuntimeError) as exc:
        parser.exit(2, f"Knowledge update failed: {exc}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
