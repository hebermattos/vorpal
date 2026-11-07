---
name: training-sources
description: 'Find, download, verify, and register cybersecurity PDF and web sources for Vorpal training. Use when asked to add training URLs, download PDF references, curate cybersecurity sources, or update corpus/url and corpus/pdfs.'
argument-hint: 'Topic, number of sources, and preferred authoritative publishers'
user-invocable: true
---

# Curate PDF Training Sources

Add reputable cybersecurity and secure-development references to Vorpal's local corpus and URL manifest. Do not start a training run unless the user asks for it.

## Procedure

1. Clarify the requested topic, number of sources, language preferences, and any publisher preferences when they are not clear. Prefer primary, authoritative sources such as OWASP, NIST, CISA, CERT/CC, national cybersecurity agencies, and official project documentation.
2. Find candidate pages and direct PDF downloads. Confirm the page or PDF is public and appears relevant. Check the source's terms and licensing where available. Do not bypass logins, paywalls, access controls, or download restrictions; do not use suspicious mirrors.
3. Inspect the existing `corpus/url` entries and `corpus/pdfs/` filenames before adding anything. Avoid duplicate URLs and existing files. Give each downloaded PDF a descriptive, filesystem-safe filename.
4. Download requested PDFs into `corpus/pdfs/`. Follow redirects and fail on HTTP errors. Verify each result is a PDF (for example with `file`) and record its SHA-256 checksum. If a download is invalid, an HTML error page, encrypted, or unreadable, do not claim it was added successfully; report the issue and preserve any existing file.
5. Add each selected public source URL as one line in `corpus/url`, preserving its existing comments and format. Keep URLs deduplicated. URLs are import instructions, not training text; the importer fetches and extracts supported content.
6. Report the source titles, publishers, URLs, downloaded filenames, and any failures. Explain that `train.py` imports new sources incrementally and tracks previously used sources. Do not use `--reset` unless explicitly requested, because it discards the current checkpoint/training history and reprocesses all sources.

## Vorpal Workflow Notes

- Local PDF files belong under `corpus/pdfs/`; the canonical importer supports PDF extraction and `train.py` scans nested corpus files.
- Public URLs belong in the repository's existing `corpus/url` file, one URL per line. Its import pipeline validates public HTTP(S) addresses, follows a limited number of redirects, extracts supported content, and records results under `corpus/imported/`.
- The project intentionally has no per-file, download, URL-list, or PDF-page size cap. Before downloading large files, check available disk and memory and tell the user when a source is unusually large. Do not add a new size cap as part of this workflow.
- Imported content is untrusted. Never execute downloaded files or archives. Preserve the importer's archive path, symlink, expansion-ratio, encrypted-file, and secret-content checks.
- Do not silently overwrite corpus files, training state, or checkpoints. Do not start a full training run, force reimport, or reset model state unless the user requests it.

## Suggested User Commands

After reviewing the added sources, the user can run the normal incremental training command:

```bash
python src/train.py --steps 500 --batch 8 --threads 4
```

A full rebuild is a separate, destructive choice and should only be suggested when requested:

```bash
python src/train.py --reset --steps 500 --batch 8 --threads 4
```
