"""Train Vorpal entirely on CPU, using random byte windows and Keras AdamW."""
import argparse
import json
import os
from pathlib import Path
import numpy as np
import tensorflow as tf
from tensorflow import keras
from model import build_model, as_inputs, configure_cpu

CHECKPOINT = Path("checkpoints/latest.keras")
METADATA = Path("checkpoints/latest.json")

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
    if args.resume:
        if not CHECKPOINT.is_file() or not METADATA.is_file():
            raise FileNotFoundError("No TensorFlow/Keras checkpoint found; PyTorch .pt is incompatible")
        meta = json.loads(METADATA.read_text(encoding="utf-8"))
        if meta["config"] != config:
            raise ValueError("Model configuration does not match checkpoint")
        model = keras.models.load_model(CHECKPOINT)
        step_start = int(meta["step"])
    else:
        model = build_model(**config)
        model.compile(optimizer=keras.optimizers.AdamW(learning_rate=args.lr, global_clipnorm=1.0),
                      loss=keras.losses.SparseCategoricalCrossentropy(from_logits=True))
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    print(f"TensorFlow/Keras CPU model: {model.count_params():,} parameters", flush=True)
    for step in range(step_start+1, args.steps+1):
        inputs, targets = make_batch(data, args.context, args.batch, rng)
        loss = float(model.train_on_batch(inputs, targets))
        if step == 1 or step % 50 == 0:
            print(f"step={step} loss={loss:.4f}", flush=True)
        if step % 100 == 0 or step == args.steps:
            temp = CHECKPOINT.with_name("latest.tmp.keras")
            model.save(temp)
            os.replace(temp, CHECKPOINT)
            metadata_tmp = METADATA.with_suffix(".json.tmp")
            metadata_tmp.write_text(json.dumps({"config": config, "step": step, "backend": "tensorflow"},
                                               indent=2), encoding="utf-8")
            os.replace(metadata_tmp, METADATA)
    print(f"Checkpoint saved to {CHECKPOINT}", flush=True)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--steps", type=int, default=500)
    p.add_argument("--batch", type=int, default=8)
    p.add_argument("--context", type=int, default=128)
    p.add_argument("--dim", type=int, default=96)
    p.add_argument("--layers", type=int, default=3)
    p.add_argument("--heads", type=int, default=4)
    p.add_argument("--dropout", type=float, default=0.1)
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--data", default="data/train.bin")
    p.add_argument("--resume", action="store_true")
    args = p.parse_args()
    run(args)

if __name__ == "__main__":
    main()
