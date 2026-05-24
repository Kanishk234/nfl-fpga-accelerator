"""
Phase 3 entry point: transfer weights from the Phase 2 fp32 model into the
QKeras model, evaluate raw quantization impact, fine-tune, then save artifacts.

Run from project root:
    python phase3_quantization/quantize.py

Compatibility note: QKeras 0.9.0 calls .numpy() inside quantizers, which
requires eager execution. tf.config.run_functions_eagerly(True) is called
at the top of each Phase 3 file before any model building.
"""

import os
import sys

import tensorflow as tf
tf.config.run_functions_eagerly(True)  # QKeras 0.9 needs eager mode for .numpy() in quantizers

import json
import pickle

import keras
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phase3_quantization.qkeras_model import build_quantized_model, compile_quantized_model
from phase3_quantization.evaluate_quantized import (
    evaluate_both_models,
    run_sample_comparison,
    save_quantization_report,
)
from phase3_quantization.verify_hls_ready import verify_hls_ready


def transfer_weights(fp32_model, qkeras_model):
    """
    Copy weights from fp32 model into the QKeras model layer by layer.
    QActivation layers have no weights — only transfer QDense and Dense layers.
    The fp32 model has Dropout layers; QKeras model does not — this is correct.
    """
    layer_names = ['dense_1', 'dense_2', 'dense_3', 'win', 'spread']

    for name in layer_names:
        fp_layer = fp32_model.get_layer(name)
        q_layer  = qkeras_model.get_layer(name)
        q_layer.set_weights(fp_layer.get_weights())

    print("Weight transfer complete. Verifying...")
    for name in layer_names:
        fp_w  = fp32_model.get_layer(name).get_weights()[0]
        q_w   = qkeras_model.get_layer(name).get_weights()[0]
        delta = np.abs(fp_w - q_w).max()
        print(f"  {name}: max weight delta = {delta:.6f}  (expect ~0.0 before fine-tuning)")

    return qkeras_model


def quantization_aware_finetune(qkeras_model, X_train, X_val, y_train, y_val):
    """
    Fine-tune QKeras model with quantization-aware training.
    30 epochs max — goal is to recover rounding losses, not retrain from scratch.
    Monitors val_win_accuracy so the best win-accuracy checkpoint is kept.
    """
    os.makedirs('artifacts', exist_ok=True)

    callbacks = [
        keras.callbacks.EarlyStopping(
            monitor='val_win_accuracy',
            patience=10,
            mode='max',
            restore_best_weights=True,
        ),
        keras.callbacks.ModelCheckpoint(
            filepath='artifacts/model_quantized.keras',
            monitor='val_win_accuracy',
            mode='max',
            save_best_only=True,
            verbose=1,
        ),
    ]

    history = qkeras_model.fit(
        X_train,
        y_train,
        validation_data=(X_val, y_val),
        epochs=30,
        batch_size=32,
        callbacks=callbacks,
        verbose=1,
    )
    return qkeras_model, history


if __name__ == '__main__':
    print("=== Phase 3: Quantization ===\n")

    # Fixed seed — makes fine-tuning reproducible, consistent with Phase 2
    keras.utils.set_random_seed(42)

    # 1. Verify feature count before anything else
    with open('artifacts/features.json') as f:
        meta = json.load(f)
    FEATURES   = meta['features']
    N_FEATURES = len(FEATURES)
    assert N_FEATURES == 21, f"Expected 21 features, got {N_FEATURES}. Check features.json."
    print(f"Feature count verified: {N_FEATURES}")

    # 2. Load artifacts — scaler is never refit
    fp32_model = keras.models.load_model('artifacts/model_best.keras')
    with open('artifacts/scaler.pkl', 'rb') as f:
        scaler = pickle.load(f)
    games = pd.read_parquet('data/processed/games.parquet')

    # 3. Recreate train/val splits — identical to Phase 2
    train = games[games['season'] <= 2020]
    val   = games[games['season'].isin([2021, 2022])]

    X_train = scaler.transform(train[FEATURES].values.astype('float32'))
    X_val   = scaler.transform(val[FEATURES].values.astype('float32'))
    y_train = {'win': train['home_win'].values.astype('float32'),
               'spread': train['spread'].values.astype('float32')}
    y_val   = {'win': val['home_win'].values.astype('float32'),
               'spread': val['spread'].values.astype('float32')}

    print(f"Train: {len(X_train)} games  Val: {len(X_val)} games")

    # 4. Build and compile QKeras model
    qkeras_model = build_quantized_model(n_features=N_FEATURES)
    compile_quantized_model(qkeras_model)
    print(f"QKeras model built: {qkeras_model.count_params()} parameters")

    # 5. Transfer weights from fp32 model
    qkeras_model = transfer_weights(fp32_model, qkeras_model)

    # 6. Evaluate BEFORE fine-tuning — measure raw quantization impact
    print("\n=== PRE-FINE-TUNING: raw quantization impact ===")
    pre_metrics = evaluate_both_models(fp32_model, qkeras_model, X_val, y_val)
    run_sample_comparison(fp32_model, qkeras_model, X_val)

    # *** PAUSE POINT — results above shown to user before fine-tuning continues ***

    # 7. Fine-tune
    print("\n=== Starting quantization-aware fine-tuning ===")
    qkeras_model, history = quantization_aware_finetune(
        qkeras_model, X_train, X_val, y_train, y_val,
    )

    # 8. Evaluate AFTER fine-tuning
    print("\n=== POST-FINE-TUNING: final quantized model ===")
    post_metrics = evaluate_both_models(fp32_model, qkeras_model, X_val, y_val)
    run_sample_comparison(fp32_model, qkeras_model, X_val)

    # 9. Save report
    bit_config = {
        'kernel':        '<8,0>',
        'bias':          '<16,6>',
        'activation':    '<8,4>',
        'output_win':    'float32 (unquantized)',
        'output_spread': 'float32 (unquantized)',
    }
    report = save_quantization_report(post_metrics, bit_config=bit_config)

    # 10. hls4ml readiness checks — must all pass before Phase 4
    print("\n=== hls4ml Readiness Checks ===")
    verify_hls_ready(qkeras_model, report)

    print("\n=== Phase 3 Complete ===")
