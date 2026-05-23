"""
Keras model definition for the NFL outcome predictor.

FPGA NOTE: Every architectural decision here is locked after Phase 4 hls4ml
conversion. Changing layer sizes, activations, or output heads after synthesis
requires full re-synthesis of the Basys 3 bitstream.

Architecture: Dense(64) → Dense(64) → Dense(32) → [win head, spread head]
Hidden activations: ReLU only — maps to a comparator in hardware.
Output activations: sigmoid (win probability), linear (spread regression).
"""

import keras


def build_model(n_features: int) -> keras.Model:
    """
    Build the dual-output NFL predictor.

    Args:
        n_features: Number of input features (must be 17 per CANONICAL_FEATURES).

    Returns:
        Uncompiled Keras model.
    """
    inputs = keras.Input(shape=(n_features,), name='features')

    # Shared feature extraction layers
    # 17 inputs × 128 neurons = 2,176 weights — still well within Basys 3 BRAM budget
    # Increased from 64 to 128 to give more capacity for spread regression
    x = keras.layers.Dense(128, activation='relu', name='dense_1')(inputs)
    x = keras.layers.Dense(64, activation='relu', name='dense_2')(x)
    x = keras.layers.Dense(32, activation='relu', name='dense_3')(x)

    # Win probability head — sigmoid outputs [0, 1], interpretable as probability
    win_output = keras.layers.Dense(1, activation='sigmoid', name='win')(x)

    # Spread regression head — linear, no constraint on output range
    # hls4ml handles the single sigmoid output with a piecewise approximation
    spread_output = keras.layers.Dense(1, activation='linear', name='spread')(x)

    return keras.Model(inputs=inputs, outputs=[win_output, spread_output])


def compile_model(model: keras.Model) -> keras.Model:
    """
    Compile the model with loss functions and metrics appropriate for
    the dual classification+regression objective.

    Loss weights: spread weight=0.05 because MSE(spread) ~ 50-100 while
    binary_crossentropy ~ 0.5-0.7. Without downweighting, spread loss
    dominates gradient updates and win accuracy suffers.
    """
    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=0.001),
        loss={
            'win':    'binary_crossentropy',
            'spread': 'mean_absolute_error',  # MAE loss directly minimises the MAE metric
                                               # MSE over-penalises blowouts, pulling preds to centre
        },
        loss_weights={
            'win':    1.0,
            'spread': 0.15,  # MAE values are smaller than MSE, so weight bumped to compensate
        },
        metrics={
            'win':    ['accuracy', 'AUC'],
            'spread': ['mae'],
        },
    )

    model.summary()
    total_params = model.count_params()
    print(f"\nTotal parameters: {total_params:,}")
    if total_params > 50_000:
        print("WARNING: Parameter count exceeds 50,000 — may not fit Basys 3 (90 DSPs, 1.8Mb BRAM)")

    return model
