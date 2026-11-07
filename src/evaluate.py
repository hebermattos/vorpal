"""Evaluate the saved Keras checkpoint on document-held-out validation bytes."""

import argparse
import math
from pathlib import Path
import numpy as np
try:
    from .config import load_config, configure_tensorflow_environment
except ImportError:  # Script invocation.
    from config import load_config, configure_tensorflow_environment

configure_tensorflow_environment()

from tensorflow import keras
from model import as_inputs, configure_cpu

SETTINGS = load_config("evaluate")


def validation_loss(model, data, *, samples, seed):
    """Measure fixed, held-out byte windows without changing model weights."""
    context = model.get_layer("position_embedding").input_dim
    if samples < 1 or len(data) <= context + 1:
        raise ValueError("Validation needs positive samples and more bytes than context + 1")
    rng = np.random.default_rng(seed)
    losses = []
    for i in rng.integers(0, len(data) - context, size=samples):
        x = data[i:i+context].astype("int32")[None, :]
        y = data[i+1:i+context+1].astype("int32")[None, :]
        logits = model(as_inputs(x), training=False)
        loss = keras.losses.sparse_categorical_crossentropy(y, logits, from_logits=True)
        losses.append(float(np.mean(loss)))
    return float(np.mean(losses))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--samples", type=int, default=SETTINGS["cli"]["samples"])
    p.add_argument("--threads", type=int, default=SETTINGS["cli"]["threads"])
    p.add_argument("--data", default=SETTINGS["cli"]["data"])
    p.add_argument("--checkpoint", default=SETTINGS["cli"]["checkpoint"])
    args = p.parse_args()
    configure_cpu(args.threads)
    if args.samples < 1:
        p.error("--samples must be >= 1")
    model = keras.models.load_model(args.checkpoint, compile=False)
    context = model.get_layer("position_embedding").input_dim
    data = np.frombuffer(Path(args.data).read_bytes(), dtype=np.uint8)
    if len(data) <= context + 1:
        p.error("Validation corpus is too short; add more held-out documents")
    mean = validation_loss(model, data, samples=args.samples, seed=SETTINGS["seed"])
    print(f"Validation loss={mean:.4f}; byte-level perplexity={math.exp(mean):.2f}")
    print("These metrics do NOT measure correctness or vulnerability-detection ability.")

if __name__ == "__main__":
    main()
