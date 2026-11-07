# AGENTS.md

This repository contains the Vorpal project, an educational prototype of an autoregressive model built with TensorFlow/Keras for programming data and defensive cybersecurity.

## General rules

- Work with Python 3.11, preferably on Linux/WSL.
- Keep focus on compatibility with TensorFlow/Keras on CPU.
- Avoid broad refactors unless necessary; prefer small, targeted changes.
- Preserve compatibility with existing import, training, evaluation, and generation flows.
- Do not introduce new dependencies unless there is a clear need.
- Do not run suspicious or unauthorized content downloaded from external sources.

## Important project structure

- `README.md`: project overview and main commands.
- `src/import_dataset.py`: data import and validation.
- `src/ingest_url.py`: backward-compatible adapter to the canonical URL importer.
- `src/content_safety.py`: shared secret-content filter.
- `src/data.py`: corpus preparation and train/validation split.
- `src/model.py`: causal Transformer model.
- `src/train.py`: training and checkpoints.
- `src/evaluate.py`: loss and byte-level perplexity.
- `src/generate.py`: local text generation.
- `tests/`: automated tests.
- `corpus/`: didactic examples and reference material.

## Expected commands

### Tests

```bash
python -m unittest discover -s tests -v
```

### Typical workflow

```bash
python src/train.py --steps 500 --batch 8 --threads 4
python src/evaluate.py --samples 50 --threads 4
python src/generate.py --prompt "def validate_port(" --max-new 160 --threads 4
```

`src/train.py` imports only new corpus sources, trains only document hashes not already consumed, and automatically resumes its checkpoint. Each `--steps` value adds that many steps. Use `--reset` to force reimport and retrain the full corpus from step zero. Run `src/data.py` separately only when you want to prepare the dataset without training.

## Code guidelines

- Keep the API and script arguments compatible with the existing project interfaces.
- Preserve compatibility for `data/*.bin`, checkpoints, and imports.
- If you change the data pipeline, validate the impact on tests and `README.md`.
- When handling external input, keep defensive validation and explicit error handling.
- Do not assume imported data is trustworthy; the filters are heuristic.
- Keep error messages useful and clear.

## Security and ethics

- The project is experimental and should not be treated as a reliable vulnerability detector.
- Generated code and input analysis should be reviewed by humans before execution in sensitive environments.

## Before finishing changes

- Run the relevant tests.
- Verify the change does not break import, training, or generation workflows.
- Update documentation when the interface or behavior of the project changes.
