"""Generate UTF-8 text from Vorpal's trained Keras model."""
import argparse
import numpy as np
import tensorflow as tf
from tensorflow import keras
from model import as_inputs, configure_cpu

def generate(model, prompt, max_new=160, temperature=0.8, seed=42):
    if not 0 < temperature <= 2:
        raise ValueError("temperature must be in (0,2]")
    context = model.get_layer("position_embedding").input_dim
    seq = list(prompt.encode("utf-8")) or [10]
    rng = np.random.default_rng(seed)
    for _ in range(max_new):
        batch = np.array([seq[-context:]], dtype=np.int32)
        logits = model(as_inputs(batch), training=False).numpy()[0, -1] / temperature
        probs = tf.nn.softmax(logits).numpy()
        seq.append(int(rng.choice(256, p=probs)))
    return bytes(seq).decode("utf-8", errors="replace")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--prompt", default="def validate_input(")
    p.add_argument("--max-new", type=int, default=160)
    p.add_argument("--temperature", type=float, default=0.8)
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--checkpoint", default="checkpoints/latest.keras")
    args = p.parse_args()
    configure_cpu(args.threads)
    model = keras.models.load_model(args.checkpoint, compile=False)
    print(generate(model, args.prompt, args.max_new, args.temperature))

if __name__ == "__main__":
    main()
