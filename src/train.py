"""Train Vorpal entirely on CPU, using random byte windows and Keras AdamW."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import numpy as np
try:
    from .config import load_config, configure_tensorflow_environment
except ImportError:  # Script invocation.
    from config import load_config, configure_tensorflow_environment

configure_tensorflow_environment()

import tensorflow as tf
from tensorflow import keras
from model import build_model, as_inputs, configure_cpu
from data import prepare
from import_dataset import import_directory
from evaluate import validation_loss

SETTINGS = load_config("train")

CHECKPOINT = Path(SETTINGS["policy"]["checkpoint"])
METADATA = Path(SETTINGS["policy"]["metadata"])


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)
            stream.flush()
        return len(text)

    def flush(self):
        for stream in self.streams:
            stream.flush()

    def isatty(self):
        return self.streams[0].isatty()


def run_with_log(args):
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = (log_dir / f"train_{timestamp}.log").resolve()
    result = 0

    with log_path.open("x", encoding="utf-8", buffering=1) as log_file:
        stdout = Tee(sys.stdout, log_file)
        stderr = Tee(sys.stderr, log_file)
        with redirect_stdout(stdout), redirect_stderr(stderr):
            print(f"Training run started: {datetime.now().astimezone().isoformat()}", flush=True)
            print(f"Training log: {log_path}", flush=True)
            try:
                run(args)
            except KeyboardInterrupt:
                result = 130
                print("Training run interrupted.", file=sys.stderr, flush=True)
                traceback.print_exc()
            except SystemExit as exc:
                result = exc.code if isinstance(exc.code, int) else 1
                print("Training run exited with an error.", file=sys.stderr, flush=True)
                traceback.print_exc()
            except Exception:
                result = 1
                print("Training run failed.", file=sys.stderr, flush=True)
                traceback.print_exc()
            else:
                print("Training run completed successfully.", flush=True)
            finally:
                print(f"Training log saved: {log_path}", flush=True)

    if result:
        raise SystemExit(result)
    return log_path


def prepare_training_corpus(args):
    from tfds_sources import discover_sources, documents
    reset = getattr(args, "reset", False)
    replay = getattr(args, "replay", False)
    if replay and (reset or not CHECKPOINT.is_file() or not METADATA.is_file()):
        raise ValueError("--replay requires an existing checkpoint and metadata, and cannot be combined with --reset")
    sources = discover_sources(args.corpus)
    output = Path(args.out)
    state_path = output / "training_state.json"
    if reset:
        print("Reset requested: clearing training history and model checkpoints.", flush=True)
        state_path.unlink(missing_ok=True)
        CHECKPOINT.unlink(missing_ok=True)
        METADATA.unlink(missing_ok=True)
    print(f"Importing corpus sources from {args.corpus}", flush=True)
    import_directory(args.corpus, Path(args.corpus) / SETTINGS["runtime"]["imported_subdir"], force=reset)
    print(f"Preparing training data in {args.out}", flush=True)
    used_hashes = set()
    if not reset and state_path.is_file():
        try:
            used_hashes.update(json.loads(state_path.read_text(encoding="utf-8")).get("used_document_hashes", []))
        except (json.JSONDecodeError, OSError):
            print(f"Ignoring invalid training state: {state_path}", flush=True)
    elif not reset and CHECKPOINT.is_file():
        previous_manifest = output / "manifest.json"
        try:
            previous = json.loads(previous_manifest.read_text(encoding="utf-8"))
            used_hashes.update(previous.get("train_document_hashes", []))
            used_hashes.update(previous.get("valid_document_hashes", []))
            if not used_hashes:
                for name in previous.get("train_documents", []) + previous.get("valid_documents", []):
                    try:
                        content = (Path(args.corpus) / name).read_text(encoding="utf-8")
                    except (OSError, UnicodeError):
                        continue
                    used_hashes.add(hashlib.sha256(content.encode("utf-8")).hexdigest())
            if used_hashes:
                print(f"Migrated {len(used_hashes)} previously prepared document(s) as used from the existing checkpoint.", flush=True)
                save_training_state(output, used_hashes)
        except (json.JSONDecodeError, OSError):
            pass
    prepare(args.corpus, output, exclude_hashes=() if replay else used_hashes,
            external_documents=documents(sources))
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    args.training_hashes = manifest.get("train_document_hashes", []) + manifest.get("valid_document_hashes", [])
    if args.data is None:
        args.data = str(output / "train.bin")
    if replay:
        print("Replay requested: reusing the corpus and preserving the checkpoint and import history.", flush=True)


def save_training_state(out, used_hashes):
    if not used_hashes:
        return
    state_path = Path(out) / "training_state.json"
    existing_hashes = set()
    if state_path.is_file():
        try:
            existing_hashes.update(json.loads(state_path.read_text(encoding="utf-8")).get("used_document_hashes", []))
        except (json.JSONDecodeError, OSError):
            pass
    existing_hashes.update(used_hashes)
    temporary_path = state_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps({"used_document_hashes": sorted(existing_hashes)}, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(state_path)

def load_bytes(path):
    """Map immutable byte datasets without allocating a full in-memory copy."""
    path = Path(path)
    if not path.stat().st_size:
        return np.empty(0, dtype=np.uint8)
    return np.memmap(path, dtype=np.uint8, mode="r")

def make_batch(data, context, batch, rng):
    if context < 1 or batch < 1:
        raise ValueError("context and batch must be positive")
    if len(data) < context + 1:
        raise ValueError("Dataset is too small for the requested context")
    starts = rng.integers(0, len(data) - context, size=batch)
    windows = data[starts[:, None] + np.arange(context + 1)].astype("int32")
    return as_inputs(windows[:, :-1]), windows[:, 1:]


def save_model_atomic(model, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + ".tmp.keras")
    model.save(temporary)
    os.replace(temporary, path)

def run(args):
    prepared = getattr(args, "prepared", False)
    if prepared and (not args.data or getattr(args, "reset", False) or getattr(args, "replay", False)):
        raise ValueError("--prepared requires --data and cannot be combined with --reset or --replay")
    if args.batch < 1 or args.steps < 1:
        raise ValueError("steps and batch must be positive")
    learning_rate = getattr(args, "lr", SETTINGS["cli"]["lr"])
    if not np.isfinite(learning_rate) or learning_rate <= 0:
        raise ValueError("--lr must be finite and positive")
    if hasattr(args, "corpus") and not prepared:
        prepare_training_corpus(args)
        if not args.training_hashes:
            print("No new documents to train; all valid corpus documents were already used.", flush=True)
            return
    configure_cpu(args.threads)
    config = dict(context=args.context, d_model=args.dim, layers=args.layers,
                  heads=args.heads, dropout=args.dropout)
    if args.dim % args.heads:
        raise ValueError("--dim must be divisible by --heads")
    data = load_bytes(args.data)
    if len(data) < args.context + 1:
        raise ValueError("Train data too small; add documents or reduce --context")
    rng = np.random.default_rng(args.seed)
    tf.random.set_seed(args.seed)
    step_start = 0
    resume = args.resume or (hasattr(args, "corpus") and not getattr(args, "reset", False) and CHECKPOINT.is_file())
    if resume:
        if not CHECKPOINT.is_file() or not METADATA.is_file():
            raise FileNotFoundError("No TensorFlow/Keras checkpoint found; PyTorch .pt is incompatible")
        meta = json.loads(METADATA.read_text(encoding="utf-8"))
        if meta["config"] != config:
            raise ValueError("Model configuration does not match checkpoint; run with --reset to train using the new architecture")
        model = keras.models.load_model(CHECKPOINT)
        # Keep optimizer iterations/moments, but honor this run's CLI/config LR.
        model.optimizer.learning_rate.assign(learning_rate)
        step_start = int(meta["step"])
        if "rng_state" in meta:
            rng.bit_generator.state = meta["rng_state"]
        else:
            rng = np.random.default_rng(np.random.SeedSequence([args.seed, step_start]))
    else:
        model = build_model(**config)
        model.compile(optimizer=keras.optimizers.AdamW(learning_rate=learning_rate, global_clipnorm=SETTINGS["runtime"]["clipnorm"]),
                      loss=keras.losses.SparseCategoricalCrossentropy(from_logits=True))
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    METADATA.parent.mkdir(parents=True, exist_ok=True)
    validation_path = getattr(args, "validation_data", None)
    if validation_path is None and hasattr(args, "out") and not prepared:
        validation_path = Path(args.out) / "valid.bin"
    validation = load_bytes(validation_path) if validation_path and Path(validation_path).is_file() else None
    if validation_path and getattr(args, "validation_data", None) and validation is None:
        raise FileNotFoundError(f"Validation file not found: {validation_path}")
    if validation is not None and len(validation) < args.context + 1:
        print("Validation skipped: held-out corpus is too short for this context.", flush=True)
        validation = None
    best_path = CHECKPOINT.with_name(SETTINGS["runtime"]["best_checkpoint_name"])
    best_metadata = CHECKPOINT.with_name(SETTINGS["runtime"]["best_metadata_name"])
    metrics_path = CHECKPOINT.with_name(SETTINGS["runtime"]["metrics_name"])
    validation_id = hashlib.sha256(validation).hexdigest() if validation is not None else None
    evaluation_config = {"validation_sha256": validation_id, "config": config,
                         "samples": SETTINGS["runtime"]["validation_samples"],
                         "seed": SETTINGS["runtime"]["validation_seed"]}
    best_loss = float("inf")
    if resume and best_metadata.is_file() and best_path.is_file():
        previous = json.loads(best_metadata.read_text(encoding="utf-8"))
        if all(previous.get(key) == value for key, value in evaluation_config.items()):
            best_loss = float(previous["validation_loss"])
    if resume and validation is not None and not np.isfinite(best_loss):
        baseline = validation_loss(model, validation, samples=evaluation_config["samples"], seed=evaluation_config["seed"])
        if np.isfinite(baseline):
            best_loss = baseline
            save_model_atomic(model, best_path)
            temporary = best_metadata.with_suffix(".json.tmp")
            temporary.write_text(json.dumps({"step": step_start, "validation_loss": best_loss,
                                              **evaluation_config}, indent=2), encoding="utf-8")
            temporary.replace(best_metadata)
            print(f"Resumed checkpoint validation baseline: {baseline:.4f}", flush=True)
    print(f"TensorFlow/Keras CPU model: {model.count_params():,} parameters", flush=True)
    final_step = step_start + args.steps
    for step in range(step_start + 1, final_step + 1):
        inputs, targets = make_batch(data, args.context, args.batch, rng)
        model.reset_metrics()
        loss = float(model.train_on_batch(inputs, targets))
        if not np.isfinite(loss):
            raise FloatingPointError(f"Non-finite training loss at step {step}; keeping the last saved checkpoint. Try a lower --lr and review the dataset.")
        if step == 1 or step % SETTINGS["runtime"]["log_interval"] == 0:
            print(f"step={step} loss={loss:.4f}", flush=True)
        if validation is not None and (step % SETTINGS["runtime"]["validation_interval"] == 0 or step == final_step):
            held_out_loss = validation_loss(model, validation,
                                           samples=evaluation_config["samples"], seed=evaluation_config["seed"])
            print(f"step={step} train_loss={loss:.4f} validation_loss={held_out_loss:.4f}", flush=True)
            with metrics_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"step": step, "train_loss": loss,
                                         "validation_loss": held_out_loss, **evaluation_config}) + "\n")
            if np.isfinite(held_out_loss) and held_out_loss < best_loss:
                best_loss = held_out_loss
                save_model_atomic(model, best_path)
                temporary = best_metadata.with_suffix(".json.tmp")
                temporary.write_text(json.dumps({"step": step, "validation_loss": best_loss,
                                                  **evaluation_config}, indent=2), encoding="utf-8")
                temporary.replace(best_metadata)
                print(f"Best validation checkpoint saved to {best_path}", flush=True)
        if step % SETTINGS["runtime"]["checkpoint_interval"] == 0 or step == final_step:
            save_model_atomic(model, CHECKPOINT)
            metadata_tmp = METADATA.with_suffix(".json.tmp")
            metadata_tmp.write_text(json.dumps({"config": config, "step": step, "backend": "tensorflow",
                                               "rng_state": rng.bit_generator.state},
                                               indent=2), encoding="utf-8")
            os.replace(metadata_tmp, METADATA)
            if hasattr(args, "training_hashes") and not prepared:
                save_training_state(args.out, args.training_hashes)
                print(f"Recorded {len(args.training_hashes)} processed document hash(es).", flush=True)
    print(f"Checkpoint saved to {CHECKPOINT}", flush=True)

def main():
    p = argparse.ArgumentParser()
    profiles = {profile["id"]: profile for profile in load_config("web")["profiles"]}
    aliases = load_config("web")["profile_aliases"]
    p.add_argument("--profile", type=lambda value: aliases.get(value, value), choices=tuple(profiles), default=None,
                   help="Select a training subject configured in config/web.json")
    p.add_argument("--steps", type=int, default=SETTINGS["cli"]["steps"])
    p.add_argument("--batch", type=int, default=SETTINGS["cli"]["batch"])
    p.add_argument("--context", type=int, default=SETTINGS["cli"]["context"])
    p.add_argument("--dim", type=int, default=SETTINGS["cli"]["dim"])
    p.add_argument("--layers", type=int, default=SETTINGS["cli"]["layers"])
    p.add_argument("--heads", type=int, default=SETTINGS["cli"]["heads"])
    p.add_argument("--dropout", type=float, default=SETTINGS["cli"]["dropout"])
    p.add_argument("--threads", type=int, default=SETTINGS["cli"]["threads"])
    p.add_argument("--lr", type=float, default=SETTINGS["cli"]["lr"])
    p.add_argument("--seed", type=int, default=SETTINGS["cli"]["seed"])
    p.add_argument("--corpus", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--log-dir", default=SETTINGS["cli"]["log_dir"], help="Directory for timestamped training logs")
    p.add_argument("--data", default=None, help="Use this train file after importing and preparing the corpus")
    p.add_argument("--prepared", action="store_true",
                   help="Train existing --data bytes without importing, resplitting or updating corpus document history.")
    p.add_argument("--validation-data", default=None, help="Override the held-out validation binary")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--resume", action="store_true", help="Explicitly resume the existing checkpoint")
    mode.add_argument("--reset", action="store_true", help="Reimport and train the full corpus from step zero")
    mode.add_argument("--replay", action="store_true", help="Train the same corpus again while resuming the existing checkpoint")
    args = p.parse_args()
    selected = profiles[args.profile] if args.profile is not None else SETTINGS["cli"]
    if args.corpus is None:
        args.corpus = selected["corpus"]
    if args.out is None:
        args.out = selected["out"]
    run_with_log(args)

if __name__ == "__main__":
    main()
