"""Local document retrieval, isolated from training data and security execution.

Indexing reads existing UTF-8 corpus/imported text only. It does not fetch URLs,
run tools, modify training splits, or load the Vorpal generation checkpoint.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

try:
    from .config import load_config
    from .content_safety import SECRET_CONTENT
    from .data import EXT, SKIP_DIRS, SKIP_NAME, is_internal_file
except ImportError:
    from config import load_config
    from content_safety import SECRET_CONTENT
    from data import EXT, SKIP_DIRS, SKIP_NAME, is_internal_file

SETTINGS = load_config("rag")
TOKEN = re.compile(r"\w+", re.UNICODE)


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _chunks(text, size, overlap):
    """Bounded line-preserving chunks, including unusually long lines."""
    lines = text.splitlines(keepends=True)
    position = 0
    while position < len(lines):
        start = position
        count = 0
        while position < len(lines) and (count + len(lines[position]) <= size or position == start):
            count += len(lines[position])
            position += 1
            if count >= size:
                break
        segment = "".join(lines[start:position])
        if len(segment) > size:
            # Oversized single lines use a bounded character window.
            for offset in range(0, len(segment), size - overlap):
                part = segment[offset:offset + size]
                if part.strip():
                    yield start + 1, part
                if offset + size >= len(segment):
                    break
        elif segment.strip():
            yield start + 1, segment
        if position >= len(lines):
            break
        rewind = 0
        while position > start + 1 and rewind + len(lines[position - 1]) <= overlap:
            position -= 1
            rewind += len(lines[position])
    # Empty lines are ignored but source line positions remain stable.


def _connection(database):
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(database)
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            source TEXT PRIMARY KEY, sha256 TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY, source TEXT NOT NULL,
            line INTEGER NOT NULL, body TEXT NOT NULL,
            FOREIGN KEY(source) REFERENCES documents(source) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS vectors (
            chunk_id INTEGER PRIMARY KEY, embedding TEXT NOT NULL,
            FOREIGN KEY(chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
    """)
    # Some Python/SQLite builds do not include the optional FTS5 extension.
    # Keep ordinary SQLite storage usable on those platforms.
    try:
        db.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_search USING fts5(
                body, content='chunks', content_rowid='id'
            );
            CREATE TRIGGER IF NOT EXISTS chunk_ai AFTER INSERT ON chunks BEGIN
                INSERT INTO chunk_search(rowid, body) VALUES (new.id, new.body);
            END;
            CREATE TRIGGER IF NOT EXISTS chunk_ad AFTER DELETE ON chunks BEGIN
                INSERT INTO chunk_search(chunk_search, rowid, body)
                VALUES ('delete', old.id, old.body);
            END;
        """)
        db.execute("SELECT count(*) FROM chunk_search").fetchone()
    except sqlite3.OperationalError as exc:
        if "no such module: fts5" not in str(exc).lower():
            db.close()
            raise
        db.execute("DROP TRIGGER IF EXISTS chunk_ai")
        db.execute("DROP TRIGGER IF EXISTS chunk_ad")
    return db


def _has_fts5(db):
    return bool(db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunk_search'"
    ).fetchone())


def _fallback_bm25(db, tokens, pool):
    """Small-corpus BM25 without optional SQLite extensions or third-party modules."""
    rows = [(ident, [token.casefold() for token in TOKEN.findall(body)])
            for ident, body in db.execute("SELECT id,body FROM chunks")]
    if not rows:
        return []
    terms = set(token.casefold() for token in tokens[:32])
    length_avg = sum(len(words) for _, words in rows) / len(rows)
    df = {term: sum(term in words for _, words in rows) for term in terms}
    scored = []
    for ident, words in rows:
        score = 0.0
        for term in terms:
            count = words.count(term)
            if not count:
                continue
            idf = math.log(1 + (len(rows) - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * count * 2.2 / (
                count + 1.2 * (0.25 + 0.75 * len(words) / max(length_avg, 1))
            )
        if score:
            scored.append((ident, -score))
    return sorted(scored, key=lambda item: (item[1], item[0]))[:pool]


def _candidates(root, database, settings):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"Corpus directory does not exist: {root}")
    database = Path(database).resolve()
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file() or path.resolve() == database:
            continue
        relative = path.relative_to(root)
        if (path.suffix.lower() not in EXT or is_internal_file(path.name)
                or SKIP_NAME.search(path.name) or
                any(part in SKIP_DIRS for part in relative.parts[:-1])):
            continue
        if path.stat().st_size > settings["max_file_bytes"]:
            continue
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeError):
            continue
        if len(text.strip()) < settings["min_text_length"] or SECRET_CONTENT.search(text):
            continue
        yield str(relative), _digest(raw), text


def _encoder(settings):
    model_path = Path(settings["embedding_model"]).expanduser()
    if not model_path.is_dir():
        raise ValueError("Hybrid RAG needs a locally installed embedding_model directory; no model is downloaded.")
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError("Hybrid RAG requires the optional sentence-transformers package.") from exc
    return SentenceTransformer(str(model_path), local_files_only=True, device="cpu")


def index(root, database=None, *, settings=None, hybrid=False):
    """Synchronize changed files; a failed embedding pass rolls back the transaction."""
    settings = settings or SETTINGS
    database = database or settings["database"]
    encoder = _encoder(settings) if hybrid else None
    root = Path(root).resolve()
    db = _connection(database)
    try:
        embedding_id = str(Path(settings["embedding_model"]).expanduser().resolve()) if hybrid else ""
        previous_id = db.execute("SELECT value FROM metadata WHERE key='encoder'").fetchone()
        if previous_id and previous_id[0] != embedding_id:
            raise ValueError("Index embedding mode/model changed; use a new database path.")
        known = dict(db.execute("SELECT source, sha256 FROM documents"))
        seen, changed = set(), 0
        with db:
            for source, sha, text in _candidates(root, database, settings):
                seen.add(source)
                if known.get(source) == sha:
                    continue
                db.execute("DELETE FROM documents WHERE source=?", (source,))
                db.execute("INSERT INTO documents VALUES (?, ?)", (source, sha))
                items = list(_chunks(text, settings["chunk_chars"], settings["chunk_overlap"]))
                for line, body in items:
                    cursor = db.execute(
                        "INSERT INTO chunks(source,line,body) VALUES (?,?,?)", (source, line, body))
                    if encoder:
                        embedding = encoder.encode(body, normalize_embeddings=True)
                        db.execute("INSERT INTO vectors VALUES (?,?)",
                                   (cursor.lastrowid, json.dumps(embedding.tolist())))
                changed += 1
            for source in known.keys() - seen:
                db.execute("DELETE FROM documents WHERE source=?", (source,))
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('encoder',?)", (embedding_id,))
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('root',?)", (str(root),))
        return {"documents": len(seen), "updated": changed, "removed": len(known.keys() - seen),
                "mode": "hybrid" if hybrid else "text"}
    finally:
        db.close()


def _cosine(left, right):
    if len(left) != len(right):
        return 0.0
    norm1 = math.sqrt(sum(x * x for x in left))
    norm2 = math.sqrt(sum(x * x for x in right))
    if not norm1 or not norm2:
        return 0.0
    return sum(x * y for x, y in zip(left, right)) / (norm1 * norm2)


def search(question, database=None, *, settings=None, limit=None):
    """Use FTS5 + optional cosine retrieval, reciprocal rank fusion and lexical reranking."""
    settings = settings or SETTINGS
    if not isinstance(question, str) or not question.strip():
        raise ValueError("Enter a non-empty retrieval query.")
    limit = settings["top_k"] if limit is None else limit
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    database = Path(database or settings["database"])
    if not database.is_file():
        raise ValueError("RAG index is missing; run the index command first.")
    db = _connection(database)
    try:
        encoder_row = db.execute("SELECT value FROM metadata WHERE key='encoder'").fetchone()
        if encoder_row is None:
            raise ValueError("RAG index is empty; run the index command first.")
        identifiers = TOKEN.findall(question)
        if not identifiers:
            return []
        # Quote each token: query syntax is never interpreted as user-controlled FTS operators.
        match = " OR ".join('"' + word.replace('"', '""') + '"' for word in identifiers[:32])
        pool = max(limit * settings["candidate_multiplier"], limit)
        if _has_fts5(db):
            lexical = db.execute(
                "SELECT c.id, bm25(chunk_search) AS score FROM chunk_search "
                "JOIN chunks c ON c.id=chunk_search.rowid WHERE chunk_search MATCH ? "
                "ORDER BY score LIMIT ?", (match, pool)).fetchall()
        else:
            lexical = _fallback_bm25(db, identifiers, pool)
        ranks = {}
        for rank, (ident, _) in enumerate(lexical, 1):
            ranks[ident] = ranks.get(ident, 0.0) + 1 / (settings["rrf_k"] + rank)
        if encoder_row[0]:
            encoder = _encoder(settings)
            query_vector = encoder.encode(question, normalize_embeddings=True).tolist()
            vector_scores = []
            for ident, payload in db.execute("SELECT chunk_id, embedding FROM vectors"):
                score = _cosine(query_vector, json.loads(payload))
                vector_scores.append((score, ident))
            vector_scores.sort(reverse=True)
            for rank, (_, ident) in enumerate(vector_scores[:pool], 1):
                ranks[ident] = ranks.get(ident, 0.0) + 1 / (settings["rrf_k"] + rank)
        if not ranks:
            return []
        wanted = sorted(ranks)
        placeholders = ",".join("?" for _ in wanted)
        rows = db.execute(
            f"SELECT c.id,c.source,c.line,c.body,d.sha256 FROM chunks c "
            f"JOIN documents d ON d.source=c.source WHERE c.id IN ({placeholders})", wanted)
        terms = {token.casefold() for token in identifiers}
        results = []
        for ident, source, line, body, sha in rows:
            coverage = len(terms.intersection(token.casefold() for token in TOKEN.findall(body)))
            score = ranks[ident] + settings["coverage_weight"] * coverage / len(terms)
            results.append({"source": source, "line": line, "sha256": sha,
                            "text": body, "score": round(score, 8)})
        results.sort(key=lambda item: (-item["score"], item["source"], item["line"]))
        return results[:limit]
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Local, read-only document retrieval")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("index")
    build.add_argument("--corpus", default=SETTINGS["corpus"])
    build.add_argument("--hybrid", action="store_true", help="Use an already installed local embedding model")
    query = commands.add_parser("search")
    query.add_argument("question")
    query.add_argument("--limit", type=int)
    for command in (build, query):
        command.add_argument("--database", default=SETTINGS["database"])
    args = parser.parse_args()
    try:
        result = (index(args.corpus, args.database, hybrid=args.hybrid)
                  if args.command == "index" else
                  search(args.question, args.database, limit=args.limit))
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
