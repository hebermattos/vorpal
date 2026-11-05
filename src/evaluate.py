"""Evaluate the saved Keras checkpoint on document-held-out validation bytes."""
import argparse
import math
from pathlib import Path
import numpy as np
from tensorflow import keras
from model import as_inputs, configure_cpu

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--samples", type=int, default=50)
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--data", default="data/valid.bin")
    p.add_argument("--checkpoint", default="checkpoints/latest.keras")
    args = p.parse_args()
    configure_cpu(args.threads)
    if args.samples < 1:
        p.error("--samples must be >= 1")
    model = keras.models.load_model(args.checkpoint, compile=False)
    context = model.get_layer("position_embedding").input_dim
    data = np.frombuffer(Path(args.data).read_bytes(), dtype=np.uint8)
    if len(data) <= context + 1:
        p.error("Validation corpus is too short; add more held-out documents")
    rng = np.random.default_rng(123)
    losses = []
    for i in rng.integers(0, len(data)-context, size=args.samples):
        x = data[i:i+context].astype("int32")[None, :]
        y = data[i+1:i+context+1].astype("int32")[None, :]
        logits = model(as_inputs(x), training=False)
        loss = keras.losses.sparse_categorical_crossentropy(y, logits, from_logits=True)
        losses.append(float(np.mean(loss)))
    mean = float(np.mean(losses))
    print(f"Validation loss={mean:.4f}; byte-level perplexity={math.exp(mean):.2f}")
    print("These metrics do NOT measure correctness or vulnerability-detection ability.")

if __name__ == "__main__":
    main()
