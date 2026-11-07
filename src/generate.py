"""Generate UTF-8 text from Vorpal's trained Keras model."""

import argparse
import json
import numpy as np
try:
    from .config import load_config, configure_tensorflow_environment
except ImportError:  # Script invocation.
    from config import load_config, configure_tensorflow_environment

configure_tensorflow_environment()

import tensorflow as tf
from tensorflow import keras
try:
    from .model import as_inputs, configure_cpu
except ImportError:  # Script invocation.
    from model import as_inputs, configure_cpu

SETTINGS = load_config("generate")

def generate(
    model, prompt,
    max_new=SETTINGS["functions"]["generate"]["max_new"],
    temperature=SETTINGS["functions"]["generate"]["temperature"],
    seed=SETTINGS["functions"]["generate"]["seed"],
    *, stop_on_json=False,
):
    if not 0 < temperature <= 2:
        raise ValueError("temperature must be in (0,2]")
    context = model.get_layer("position_embedding").input_dim
    seq = list(prompt.encode("utf-8")) or [10]
    initial_length = len(seq)
    rng = np.random.default_rng(seed)
    for _ in range(max_new):
        batch = np.array([seq[-context:]], dtype=np.int32)
        logits = model(as_inputs(batch), training=False).numpy()[0, -1] / temperature
        probs = tf.nn.softmax(logits).numpy()
        seq.append(int(rng.choice(256, p=probs)))
        if stop_on_json and seq[-1] == ord('}'):
            try:
                result = json.loads(bytes(seq[initial_length:]).decode('utf-8'))
                if isinstance(result, dict):
                    break
            except (ValueError, UnicodeError):
                pass
    return bytes(seq).decode("utf-8", errors="replace")

def security_prompt(question):
    """Format a standalone defensive security question."""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("Enter a non-empty security question.")
    return "Dê dicas de segurança defensiva em português. Se não souber, diga.\n" + f"Pergunta: {question.strip()}\nResposta:"


def main():
    p = argparse.ArgumentParser()
    inputs = p.add_mutually_exclusive_group()
    inputs.add_argument("--prompt", default=SETTINGS["cli"]["prompt"])
    inputs.add_argument("--question", help="Ask a standalone security question in Portuguese.")
    p.add_argument("--max-new", type=int, default=SETTINGS["cli"]["max_new"])
    p.add_argument("--temperature", type=float, default=SETTINGS["cli"]["temperature"])
    p.add_argument("--threads", type=int, default=SETTINGS["cli"]["threads"])
    p.add_argument("--checkpoint", default=SETTINGS["cli"]["checkpoint"])
    args = p.parse_args()
    try:
        prompt = security_prompt(args.question) if args.question is not None else args.prompt
    except ValueError as exc:
        p.error(str(exc))
    configure_cpu(args.threads)
    model = keras.models.load_model(args.checkpoint, compile=False)
    result = generate(model, prompt, args.max_new, args.temperature)
    print(result[len(prompt):].strip() if args.question is not None else result)

if __name__ == "__main__":
    main()
