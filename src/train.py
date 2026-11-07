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
import tensorflow as tf
from tensorflow import keras
from model import build_model, as_inputs, configure_cpu
from data import prepare
from import_dataset import import_directory

CHECKPOINT = Path("checkpoints/latest.keras")
METADATA = Path("checkpoints/latest.json")


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
    reset = getattr(args, "reset", False)
    output = Path(args.out)
    state_path = output / "training_state.json"
    if reset:
        print("Reset requested: clearing training history and model checkpoints.", flush=True)
        state_path.unlink(missing_ok=True)
        CHECKPOINT.unlink(missing_ok=True)
        METADATA.unlink(missing_ok=True)
    print(f"Importing corpus sources from {args.corpus}", flush=True)
    import_directory(args.corpus, Path(args.corpus) / "imported", force=reset)
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
    prepare(args.corpus, output, exclude_hashes=used_hashes)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    args.training_hashes = manifest.get("train_document_hashes", []) + manifest.get("valid_document_hashes", [])
    if args.data is None:
        args.data = str(output / "train.bin")


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
    raw = Path(path).read_bytes()
    return np.frombuffer(raw, dtype=np.uint8)

def make_batch(data, context, batch, rng):
    if len(data) <= context + 1:
        raise ValueError("Dataset is too small for the requested context")
    starts = rng.integers(0, len(data) - context, size=batch)
    x = np.stack([data[i:i+context] for i in starts]).astype("int32")
    y = np.stack([data[i+1:i+context+1] for i in starts]).astype("int32")
    return as_inputs(x), y

def run(args):
    if hasattr(args, "corpus"):
        prepare_training_corpus(args)
        if not args.training_hashes:
            print("No new documents to train; all valid corpus documents were already used.", flush=True)
            return
    configure_cpu(args.threads)
    if args.batch < 1 or args.steps < 1:
        raise ValueError("steps and batch must be positive")
    config = dict(context=args.context, d_model=args.dim, layers=args.layers,
                  heads=args.heads, dropout=args.dropout)
    if args.dim % args.heads:
        raise ValueError("--dim must be divisible by --heads")
    data = load_bytes(args.data)
    if len(data) <= args.context + 1:
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
        step_start = int(meta["step"])
    else:
        model = build_model(**config)
        model.compile(optimizer=keras.optimizers.AdamW(learning_rate=args.lr, global_clipnorm=1.0),
                      loss=keras.losses.SparseCategoricalCrossentropy(from_logits=True))
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    print(f"TensorFlow/Keras CPU model: {model.count_params():,} parameters", flush=True)
    final_step = step_start + args.steps
    for step in range(step_start + 1, final_step + 1):
        inputs, targets = make_batch(data, args.context, args.batch, rng)
        loss = float(model.train_on_batch(inputs, targets))
        if step == 1 or step % 50 == 0:
            print(f"step={step} loss={loss:.4f}", flush=True)
        if step % 100 == 0 or step == final_step:
            temp = CHECKPOINT.with_name("latest.tmp.keras")
            model.save(temp)
            os.replace(temp, CHECKPOINT)
            metadata_tmp = METADATA.with_suffix(".json.tmp")
            metadata_tmp.write_text(json.dumps({"config": config, "step": step, "backend": "tensorflow"},
                                               indent=2), encoding="utf-8")
            os.replace(metadata_tmp, METADATA)
            if hasattr(args, "training_hashes"):
                save_training_state(args.out, args.training_hashes)
                print(f"Recorded {len(args.training_hashes)} newly used document(s).", flush=True)
    print(f"Checkpoint saved to {CHECKPOINT}", flush=True)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--steps", type=int, default=500)
    p.add_argument("--batch", type=int, default=8)
    p.add_argument("--context", type=int, default=256)
    p.add_argument("--dim", type=int, default=96)
    p.add_argument("--layers", type=int, default=3)
    p.add_argument("--heads", type=int, default=4)
    p.add_argument("--dropout", type=float, default=0.1)
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--corpus", default="corpus")
    p.add_argument("--out", default="data")
    p.add_argument("--log-dir", default="logs", help="Directory for timestamped training logs")
    p.add_argument("--data", default=None, help="Use this train file after importing and preparing the corpus")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--resume", action="store_true", help="Explicitly resume the existing checkpoint")
    mode.add_argument("--reset", action="store_true", help="Reimport and train the full corpus from step zero")
    args = p.parse_args()
    run_with_log(args)

if __name__ == "__main__":
    main()
