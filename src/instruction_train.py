"""Optional, response-masked instruction fine-tuning of the shared CPU checkpoint.

Only reviewed local JSONL examples are accepted. No scanners, imports, or downloads.
The checkpoint architecture and binary corpus pipeline are unchanged.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np

try:
    from .config import load_config
    from .security_tools import planning_prompt, validate_arguments, validate_target
except ImportError:
    from config import load_config
    from security_tools import planning_prompt, validate_arguments, validate_target


def read_examples(path, *, settings=None):
    settings = settings or load_config("instruction_train")
    path = Path(path)
    result = []
    seen = {}
    with path.open("r", encoding="utf-8") as stream:
        for line_number, raw in enumerate(stream, 1):
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            if len(raw) > settings["limits"]["max_line_chars"]:
                raise ValueError(f"Instruction line {line_number} exceeds the configured size limit")
            try:
                item = json.loads(raw)
                if not isinstance(item, dict) or set(item) != {"tool", "target", "goal", "args", "reason", "split"}:
                    raise ValueError("Unexpected instruction fields")
                if item["split"] not in ("train", "validation", "test"):
                    raise ValueError("Invalid instruction split")
                if not isinstance(item["goal"], str) or not item["goal"].strip() or not item["goal"].isprintable():
                    raise ValueError("Invalid goal")
                if not isinstance(item["reason"], str) or not item["reason"].strip() or not item["reason"].isprintable():
                    raise ValueError("Invalid reason")
                target = validate_target(item["tool"], item["target"])
                args = validate_arguments(item["tool"], item["args"])
                if args != item["args"] or target != item["target"]:
                    raise ValueError("Instruction must use normalized arguments and target")
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"Invalid instruction line {line_number}: {exc}") from exc
            digest = hashlib.sha256(json.dumps({
                key: item[key] for key in ("tool", "target", "goal", "args", "reason")
            }, sort_keys=True).encode("utf-8")).hexdigest()
            if digest in seen:
                if seen[digest] != item["split"]:
                    raise ValueError(f"Instruction split leakage at line {line_number}")
                raise ValueError(f"Duplicate instruction at line {line_number}")
            seen[digest] = item["split"]
            result.append({**item, "hash": digest})
            if len(result) > settings["limits"]["max_examples"]:
                raise ValueError("Too many instruction examples")
    if not result:
        raise ValueError("No reviewed instruction examples")
    return result


def encode_example(example, context, *, settings=None):
    """Return (x, next-byte targets, loss mask); prompt bytes have zero loss."""
    settings = settings or load_config("instruction_train")
    prompt, used = planning_prompt(example["tool"], example["target"], example["goal"], context)
    if used != example["goal"]:
        raise ValueError("Instruction prompt exceeds context; refusing to truncate training intent")
    response = json.dumps({"args": example["args"], "reason": example["reason"]},
                          ensure_ascii=True, separators=(",", ":"))
    prefix = prompt.encode("utf-8")
    sequence = prefix + response.encode("utf-8")
    if len(sequence) > context + 1:
        raise ValueError("Instruction and answer exceed checkpoint context; use shorter reviewed examples")
    if len(prefix) < 1:
        raise ValueError("Empty instruction prompt")
    x = np.zeros(context, dtype=np.int32)
    y = np.zeros(context, dtype=np.int32)
    weights = np.zeros(context, dtype=np.float32)
    length = len(sequence) - 1
    x[:length] = np.frombuffer(sequence[:-1], dtype=np.uint8)
    y[:length] = np.frombuffer(sequence[1:], dtype=np.uint8)
    weights[len(prefix)-1:length] = 1
    return x, y, weights


def prepare_examples(path, context, *, settings=None):
    examples = read_examples(path, settings=settings)
    encoded = []
    for example in examples:
        try:
            arrays = encode_example(example, context, settings=settings)
        except ValueError as exc:
            raise ValueError(f"Invalid instruction {example['hash'][:12]}: {exc}") from exc
        encoded.append((example, arrays))
    return encoded


def sample_indices(examples, batch, rng, step, total_steps, *, settings=None):
    """Deterministic tool-balanced curriculum with graceful single-tool support."""
    settings = settings or load_config("instruction_train")
    stages = settings["curriculum"]["stages"]
    if not stages:
        raise ValueError("Instruction curriculum must define at least one stage")
    stage = stages[min(len(stages) - 1, (step * len(stages)) // total_steps)]
    groups = {}
    for index, (item, _) in enumerate(examples):
        groups.setdefault(item["tool"], []).append(index)
    if stage != "mixed" and stage in groups:
        return rng.choice(groups[stage], size=batch)
    tools = sorted(groups)
    selected_tools = rng.choice(tools, size=batch)
    return np.array([rng.choice(groups[tool]) for tool in selected_tools], dtype=np.int64)


def train_instructions(args, *, checkpoint, metadata, train_settings):
    """Update the shared checkpoint, preserving existing model metadata and optimizer."""
    from tensorflow import keras
    try:
        from .model import as_inputs, configure_cpu
        from .train import save_model_atomic
    except ImportError:
        from model import as_inputs, configure_cpu
        from train import save_model_atomic

    if getattr(args, "reset", False) or getattr(args, "prepared", False):
        raise ValueError("Instruction training requires an existing checkpoint; --reset/--prepared are unsupported")
    if not checkpoint.is_file() or not metadata.is_file():
        raise FileNotFoundError("Instruction training requires an existing Keras checkpoint and metadata")
    if args.batch < 1 or args.steps < 1 or args.threads < 1 or not np.isfinite(args.lr) or args.lr <= 0:
        raise ValueError("Batch, steps and threads must be positive")
    meta = json.loads(metadata.read_text(encoding="utf-8"))
    expected = dict(context=args.context, d_model=args.dim, layers=args.layers,
                    heads=args.heads, dropout=args.dropout)
    if meta.get("config") != expected:
        raise ValueError("Instruction training must use the checkpoint's original architecture")
    context = expected["context"]
    records = prepare_examples(args.instruction_examples, context)
    state_path = Path(load_config("instruction_train")["paths"]["consumed_hashes"])
    consumed = set()
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        hashes = state.get("used_document_hashes")
        if not isinstance(hashes, list) or any(not isinstance(value, str) or len(value) != 64 for value in hashes):
            raise ValueError("Invalid instruction training history")
        consumed = set(hashes)
    selected = [(item, arrays) for item, arrays in records
                if item["split"] == "train" and (getattr(args, "replay", False) or item["hash"] not in consumed)]
    held_out = [item for item, _ in records if item["split"] != "train"]
    if not selected:
        print("No new reviewed instruction examples available; use --replay to repeat training.")
        return
    configure_cpu(args.threads)
    model = keras.models.load_model(checkpoint)
    if model.optimizer is None:
        raise ValueError("Checkpoint optimizer state is unavailable")
    model.optimizer.learning_rate.assign(args.lr)
    rng = np.random.default_rng(args.seed)
    x = np.stack([arrays[0] for _, arrays in selected])
    y = np.stack([arrays[1] for _, arrays in selected])
    weights = np.stack([arrays[2] for _, arrays in selected])
    initial_step = int(meta["step"])
    for index in range(1, args.steps + 1):
        chosen = sample_indices(selected, args.batch, rng, index - 1, args.steps)
        model.reset_metrics()
        loss = float(model.train_on_batch(as_inputs(x[chosen]), y[chosen], sample_weight=weights[chosen]))
        if not np.isfinite(loss):
            raise FloatingPointError("Non-finite instruction loss; checkpoint left unchanged")
        if index == 1 or index % train_settings["runtime"]["log_interval"] == 0:
            print(f"instruction_step={index} weighted_loss={loss:.4f}", flush=True)
    # Commit instruction history only after successfully saving checkpoint.
    save_model_atomic(model, checkpoint)
    meta["step"] = initial_step + args.steps
    # Byte-window sampling for a subsequent ordinary run is incompatible with
    # the new iteration count; clear its stored sampler state rather than reuse it.
    meta.pop("rng_state", None)
    temp = metadata.with_suffix(".json.tmp")
    temp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    os.replace(temp, metadata)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temp_state = state_path.with_suffix(".json.tmp")
    temp_state.write_text(json.dumps({"used_document_hashes": sorted(consumed |
        {item["hash"] for item, _ in selected})}, indent=2), encoding="utf-8")
    os.replace(temp_state, state_path)
    print(f"Instruction checkpoint saved: {checkpoint}; train examples={len(selected)}; held-out={len(held_out)}")
