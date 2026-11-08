"""Read-only corpus and prepared-data quality audit (no imports or downloads)."""
import argparse
import hashlib
import json
from pathlib import Path

try:
    from .data import (_split_history, configured_outputs, default_split_root, EXT,
                       is_internal_file, SETTINGS, SKIP_DIRS, SKIP_NAME)
    from .split_registry import shared_history
    from .content_safety import SECRET_CONTENT
except ImportError:
    from data import (_split_history, configured_outputs, default_split_root, EXT,
                      is_internal_file, SETTINGS, SKIP_DIRS, SKIP_NAME)
    from split_registry import shared_history
    from content_safety import SECRET_CONTENT


def audit(corpus, out, *, split_root=None):
    corpus, out = Path(corpus).resolve(), Path(out).resolve()
    train, valid = _split_history(out)
    findings = []
    if split_root is not None:
        root = Path(split_root).resolve()
        try:
            shared_history(root, configured_outputs(root, out))
        except ValueError as exc:
            findings.append(str(exc))
    seen = {}
    counts = {"eligible": 0, "short": 0, "secrets": 0, "duplicate": 0, "unreadable": 0}
    for path in sorted(corpus.rglob("*")):
        if not path.is_file() or path.is_symlink() or path.suffix.lower() not in EXT:
            continue
        if out == path.resolve() or out in path.resolve().parents:
            continue
        relative = path.relative_to(corpus)
        if (any(part in SKIP_DIRS for part in relative.parts[:-1])
                or SKIP_NAME.search(path.name) or is_internal_file(path.name)):
            continue
        try:
            value = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            counts["unreadable"] += 1
            continue
        if SECRET_CONTENT.search(value):
            counts["secrets"] += 1
            continue
        if len(value.strip()) < SETTINGS["runtime"]["min_text_length"]:
            counts["short"] += 1
            continue
        counts["eligible"] += 1
        normalized = hashlib.sha256(value.rstrip().encode("utf-8")).hexdigest()
        if normalized in seen:
            counts["duplicate"] += 1
        else:
            seen[normalized] = str(relative)
    if train & valid:
        findings.append("Train/validation document hash overlap")
    manifest_path = out / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for kind, assigned in (("train", train), ("valid", valid)):
            recorded = set(manifest.get(f"{kind}_document_hashes", []))
            if not recorded <= assigned:
                findings.append(f"Manifest {kind} hashes disagree with persisted split history")
            binary = out / f"{kind}.bin"
            if not binary.is_file() or binary.stat().st_size != manifest.get(f"{kind}_bytes"):
                findings.append(f"Prepared {kind} binary is missing or has a different size")
        if set(manifest.get("train_document_hashes", [])) & set(manifest.get("valid_document_hashes", [])):
            findings.append("Manifest train/validation hash overlap")
    return {"corpus": str(corpus), "out": str(out), "counts": counts,
            "split_history": {"train": len(train), "validation": len(valid)},
            "integrity_passed": not findings, "findings": findings,
            "quality_warnings": {key: value for key, value in counts.items() if key != "eligible" and value > 0},
            "limitations": ["Heuristic secret detection and exact/whitespace-trimmed duplicate counts only.",
                            "Does not measure semantic duplication, licenses, or dataset correctness.",
                            "Binary size checks do not authenticate binary contents."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default="corpus")
    parser.add_argument("--out", default="data")
    parser.add_argument("--report", default="reports/data_quality.json")
    args = parser.parse_args(argv)
    report = audit(args.corpus, args.out, split_root=default_split_root())
    target = Path(args.report)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["integrity_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
