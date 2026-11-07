"""Vorpal byte-level causal Transformer in TensorFlow/Keras (no pretrained weights)."""

try:
    from .config import load_config, configure_tensorflow_environment
except ImportError:  # Script invocation.
    from config import load_config, configure_tensorflow_environment

configure_tensorflow_environment()

import tensorflow as tf
from tensorflow import keras

SETTINGS = load_config("model")

def build_model(
    context=SETTINGS["functions"]["build_model"]["context"],
    d_model=SETTINGS["functions"]["build_model"]["d_model"],
    layers=SETTINGS["functions"]["build_model"]["layers"],
    heads=SETTINGS["functions"]["build_model"]["heads"],
    dropout=SETTINGS["functions"]["build_model"]["dropout"],
):
    if context < 2 or d_model < 4 or layers < 1 or heads < 1 or d_model % heads:
        raise ValueError("Invalid context/d_model/layers/heads configuration")
    if not 0 <= dropout < 1:
        raise ValueError("dropout must be in [0,1)")
    token_ids = keras.Input(shape=(None,), dtype="int32", name="tokens")
    positions = keras.Input(shape=(None,), dtype="int32", name="positions")
    tokens = keras.layers.Embedding(256, d_model, name="token_embedding")(token_ids)
    pos = keras.layers.Embedding(context, d_model, name="position_embedding")(positions)
    x = keras.layers.Add()([tokens, pos])
    for i in range(layers):
        norm = keras.layers.LayerNormalization(name=f"attention_norm_{i}")(x)
        attn = keras.layers.MultiHeadAttention(
            num_heads=heads, key_dim=d_model // heads,
            dropout=dropout, name=f"causal_attention_{i}"
        )(norm, norm, use_causal_mask=True)
        x = keras.layers.Add()([x, attn])
        norm = keras.layers.LayerNormalization(name=f"feedforward_norm_{i}")(x)
        mlp = keras.layers.Dense(SETTINGS["runtime"]["feedforward_multiplier"] * d_model, activation="gelu", name=f"ff_1_{i}")(norm)
        mlp = keras.layers.Dense(d_model, name=f"ff_2_{i}")(mlp)
        mlp = keras.layers.Dropout(dropout)(mlp)
        x = keras.layers.Add()([x, mlp])
    logits = keras.layers.Dense(256, name="byte_logits")(keras.layers.LayerNormalization(name="final_norm")(x))
    model = keras.Model(inputs=[token_ids, positions], outputs=logits, name="vorpal_cpu")
    return model

def as_inputs(batch):
    """Return token and position arrays. Shape: batch x sequence."""
    import numpy as np
    batch = np.asarray(batch, dtype=np.int32)
    if batch.ndim != 2:
        raise ValueError("batch must be 2D")
    pos = np.broadcast_to(np.arange(batch.shape[1], dtype=np.int32), batch.shape).copy()
    return {"tokens": batch, "positions": pos}

def configure_cpu(threads=SETTINGS["functions"]["configure_cpu"]["threads"]):
    if threads < 1:
        raise ValueError("threads must be >= 1")
    tf.config.set_visible_devices([], "GPU")
    try:
        tf.config.threading.set_intra_op_parallelism_threads(threads)
        tf.config.threading.set_inter_op_parallelism_threads(min(threads, SETTINGS["runtime"]["max_inter_op_threads"]))
    except RuntimeError:
        pass
