"""
QKeras model definition for Phase 3 quantization.

Mirrors the Phase 2 architecture exactly — same layer names, same sizes —
but replaces Dense+ReLU with QDense+QActivation so weights and activations
are stored as fixed-point integers. Dropout is omitted: it is training-only
and has no effect at inference time (and no FPGA equivalent).

QKeras 0.9.0 calls .numpy() inside quantizers, which requires eager execution.
tf.config.run_functions_eagerly(True) must be called before any model build.

Bit widths chosen for Basys 3 (Artix-7 XC7A35T):
  kernel:     <8,0>  — inputs scaled [0,1], weights near zero; 8 bits sufficient
  bias:       <16,6> — biases accumulate; need more integer range than weights
  activation: <8,4>  — ReLU non-negative; 4 integer bits covers realistic range
  outputs:    float32 (unquantized) — hls4ml handles output precision in Phase 4

Layer names must match Phase 2 exactly — weight transfer uses get_layer(name).
"""

import tensorflow as tf
tf.config.run_functions_eagerly(True)  # QKeras 0.9 needs eager mode for .numpy() in quantizers

import keras
from keras.layers import Dense
from qkeras import QDense, QActivation, quantized_bits, quantized_relu


def build_quantized_model(n_features: int) -> keras.Model:
    """
    Build QKeras model. n_features must come from features.json — never hardcode.
    Call compile_quantized_model() before training or evaluation.
    """
    inputs = keras.Input(shape=(n_features,), name='features')

    # Layer 1: n_features → 128
    x = QDense(
        128,
        kernel_quantizer=quantized_bits(bits=8, integer=0, symmetric=1),
        bias_quantizer=quantized_bits(bits=16, integer=6),
        name='dense_1',
    )(inputs)
    x = QActivation(quantized_relu(bits=8, integer=4), name='relu_1')(x)
    # Dropout omitted — training-only, not present at inference, no FPGA impact

    # Layer 2: 128 → 64
    x = QDense(
        64,
        kernel_quantizer=quantized_bits(bits=8, integer=0, symmetric=1),
        bias_quantizer=quantized_bits(bits=16, integer=6),
        name='dense_2',
    )(x)
    x = QActivation(quantized_relu(bits=8, integer=4), name='relu_2')(x)

    # Layer 3: 64 → 32
    x = QDense(
        32,
        kernel_quantizer=quantized_bits(bits=8, integer=0, symmetric=1),
        bias_quantizer=quantized_bits(bits=16, integer=6),
        name='dense_3',
    )(x)
    x = QActivation(quantized_relu(bits=8, integer=4), name='relu_3')(x)

    # Output heads — standard Dense, NOT quantized
    # hls4ml handles output layer precision separately in Phase 4
    win_output    = Dense(1, activation='sigmoid', name='win')(x)
    spread_output = Dense(1, activation='linear',  name='spread')(x)

    return keras.Model(inputs=inputs, outputs=[win_output, spread_output])


def compile_quantized_model(model: keras.Model) -> keras.Model:
    """
    Compile with same loss/weights as Phase 2. LR is 10x lower — fine-tuning
    pre-trained weights, not training from scratch.
    """
    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=0.0001),
        loss={
            'win':    'binary_crossentropy',
            'spread': keras.losses.Huber(delta=1.0),
        },
        loss_weights={'win': 1.0, 'spread': 0.15},
        metrics={'win': ['accuracy'], 'spread': ['mae']},
    )
    return model
