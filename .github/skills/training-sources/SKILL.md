---
name: training-sources
description: 'Find, download, verify, and register PDF and web sources for Vorpal training by subject: security, Python, Nmap, or Nuclei. Use for training-source curation and source manifests.'
argument-hint: 'Topic, number of sources, and preferred authoritative publishers'
user-invocable: true
---

# Curate PDF Training Sources

Add relevant, reputable references to the requested Vorpal subject. Do not start training unless the user asks for it.

## Select the Subject

Read the profile registry in `config/web.json` before choosing paths. The current subjects are:

| Profile | Corpus root | Prepared data |
| --- | --- | --- |
| `security` | `corpus/security` | `data/security` |
| `python` | `corpus/python` | `data/python` |
| `nmap` | `corpus/tools/nmap` | `data/tools/nmap` |
| `nuclei` | `corpus/tools/nuclei` | `data/tools/nuclei` |

`complete` reads the whole `corpus/` tree. Its legacy `corpus/imported/` cache remains supported, but new subject-specific sources belong in their subject directory.

## Procedure

1. Infer the subject, source count, language and publisher preferences from the request and context. Ask only when missing information materially affects the selection. Prefer official project documentation, authors, OWASP, NIST, CISA, CERT/CC or national cybersecurity agencies, as appropriate to the subject.
2. Find public source pages and direct PDF downloads. Check relevance, publication dates and available terms/licensing. Do not bypass logins, paywalls or access controls, and do not use suspicious mirrors.
3. Inspect `<corpus-root>/url`, `<corpus-root>/pdfs/` and any retained legacy sources before adding files or URLs. Avoid duplicates. Use descriptive, filesystem-safe filenames.
4. Download PDFs into `<corpus-root>/pdfs/`, following redirects and failing on HTTP errors. Verify the PDF format and readable text with the existing importer/parser, check encryption, and record SHA-256 checksums. Preserve existing files if a download is invalid or differs unexpectedly. Do not claim success for HTML error pages, encrypted files or unreadable documents.
5. Record provenance and available license notes beside the PDFs, for example in `SOURCES-<subject>.tsv`, and maintain the folder's `SHA256SUMS`. If updating the aggregate `corpus/SHA256SUMS`, its paths are relative to `corpus/`. For URL-based ingestion, add requested public URLs to `<corpus-root>/url`, one per line, preserving comments and deduplicating entries. Download-only requests can record the source URL in the provenance file without also scheduling another fetch through `url`.
6. Report titles, publishers, source URLs, filenames, any failures and relevant age/license limitations. Explain that the selected training profile imports new sources incrementally and tracks consumed documents; downloading sources alone does not train the model.

## Vorpal Workflow Notes

- `train.py --profile <name>` uses the registry's corpus and output paths and imports supported files plus nested `url` lists. Text extraction, import reports and source state are stored under `<corpus-root>/imported/`; prepared binaries, consumed hashes and split state are under the profile's output directory.
- Generated `imported/` caches are not reimported as raw sources. Dataset preparation still reads their extracted text. `src/data.py` alone does not fetch URLs or extract PDFs.
- Profiles share the configured model checkpoint. `--replay` resumes that checkpoint and includes consumed documents. `--reset` discards the shared latest model and clears only the selected output's consumed-document state; it does not clear other profiles' histories or persisted split assignments. Use reset only when explicitly requested.
- The canonical importer has no per-file, download, URL-list or PDF-page size cap. Check disk/memory for unusually large sources; do not add a new global cap as part of curation.
- Imported content is untrusted. Never execute downloaded files or archives. Preserve archive-path, symlink, expansion-ratio, encrypted-file and secret-content checks. Do not silently overwrite corpus files, training state or checkpoints.

## Suggested User Commands

For new Nmap sources, for example:

```bash
python src/train.py --profile nmap --steps 500 --batch 8 --threads 4
```

To continue learning from that subject's already-consumed documents, with an existing checkpoint:

```bash
python src/train.py --profile nmap --replay --steps 500 --batch 8 --threads 4
```

Use `security`, `python` or `nuclei` instead when that is the requested subject. A full rebuild is a separate destructive choice, not part of ordinary source curation.
