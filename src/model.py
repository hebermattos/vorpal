"""Vorpal byte-level causal Transformer in TensorFlow/Keras (no pretrained weights)."""
import tensorflow as tf
from tensorflow import keras

def build_model(context=128, d_model=96, layers=3, heads=4, dropout=0.1):
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
        mlp = keras.layers.Dense(4 * d_model, activation="gelu", name=f"ff_1_{i}")(norm)
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

def configure_cpu(threads=4):
    if threads < 1:
        raise ValueError("threads must be >= 1")
    tf.config.set_visible_devices([], "GPU")
    try:
        tf.config.threading.set_intra_op_parallelism_threads(threads)
        tf.config.threading.set_inter_op_parallelism_threads(min(threads, 2))
    except RuntimeError:
        pass
