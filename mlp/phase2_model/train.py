"""
Phase 2 training entry point.

Loads games.parquet, applies a temporal split, fits a MinMaxScaler on
training data only, trains the dual-output model, and saves artifacts.

Run from project root:
    python mlp/phase2_model/train.py
"""

import json
import os
import pickle
import sys

import keras
import matplotlib
matplotlib.use('Agg')  # non-interactive backend for saving plots
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from mlp.phase2_model.model import build_model, compile_model
from mlp.phase2_model.evaluate import evaluate_model, evaluate_on_test


# ---------------------------------------------------------------------------
# Data loading and splitting
# ---------------------------------------------------------------------------

def load_data():
    """
    Load processed games and split temporally.

    Temporal split — NEVER random split:
        train:  seasons ≤ 2020
        val:    seasons 2021–2022
        test:   seasons 2023–2024
    """
    games = pd.read_parquet('data/processed/games.parquet')

    with open('artifacts/features.json') as f:
        meta = json.load(f)

    FEATURES = meta['features']  # ordered — do not sort or reorder

    train = games[games['season'] <= 2020]
    val   = games[games['season'].isin([2021, 2022])]
    test  = games[games['season'].isin([2023, 2024])]

    print(f"Train: {len(train)} games  ({int(train['season'].min())}–{int(train['season'].max())})")
    print(f"Val:   {len(val)} games  ({int(val['season'].min())}–{int(val['season'].max())})")
    print(f"Test:  {len(test)} games  ({int(test['season'].min())}–{int(test['season'].max())})")

    # Extract feature matrices in canonical order
    X_train = train[FEATURES].values.astype('float32')
    X_val   = val[FEATURES].values.astype('float32')
    X_test  = test[FEATURES].values.astype('float32')

    y_train = {'win': train['home_win'].values.astype('float32'),
               'spread': train['spread'].values.astype('float32')}
    y_val   = {'win': val['home_win'].values.astype('float32'),
               'spread': val['spread'].values.astype('float32')}
    y_test  = {'win': test['home_win'].values.astype('float32'),
               'spread': test['spread'].values.astype('float32')}

    return X_train, X_val, X_test, y_train, y_val, y_test, FEATURES, games, val, test


# ---------------------------------------------------------------------------
# Scaler
# ---------------------------------------------------------------------------

def fit_and_save_scaler(X_train, X_val, X_test):
    """
    Fit MinMaxScaler on training data only, then transform all splits.

    The scaler is saved immediately — it is critical infrastructure.
    Never refit it on new data. The FPGA operates on the same INT8
    mapping derived from this scaler.
    """
    scaler = MinMaxScaler(feature_range=(0, 1))
    scaler.fit(X_train)  # FIT ON TRAIN ONLY

    X_train_scaled = scaler.transform(X_train)
    X_val_scaled   = scaler.transform(X_val)
    X_test_scaled  = scaler.transform(X_test)

    with open('artifacts/scaler.pkl', 'wb') as f:
        pickle.dump(scaler, f)

    print("Scaler saved to artifacts/scaler.pkl")
    print("WARNING: Never refit this scaler. Use as-is for all future inference.")

    # Train data must be fully within [0, 1]
    assert X_train_scaled.min() >= -1e-6, \
        f"Scaled train min {X_train_scaled.min():.6f} < 0"
    assert X_train_scaled.max() <= 1.0 + 1e-6, \
        f"Scaled train max {X_train_scaled.max():.6f} > 1"
    # Val/test may slightly exceed [0, 1] if values fall outside train range — acceptable

    return X_train_scaled, X_val_scaled, X_test_scaled, scaler


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def compute_recency_weights(train_seasons, min_weight=0.25):
    """
    Linear sample weights by season: oldest season → min_weight, newest → 1.0.

    The NFL has shifted dramatically toward pass-heavy offense since ~2015 (rule
    changes, OPI enforcement). Upweighting recent seasons makes the model tune to
    modern patterns, which are more predictive of 2021–2022 validation games.
    min_weight=0.25 means 2000 games count 1/4 as much as 2020 games.
    """
    s_min, s_max = train_seasons.min(), train_seasons.max()
    if s_min == s_max:
        return np.ones(len(train_seasons))
    norm = (train_seasons - s_min) / (s_max - s_min)  # 0.0 → 1.0
    return norm * (1.0 - min_weight) + min_weight       # min_weight → 1.0


def train_model(model, X_train, X_val, y_train, y_val, sample_weight=None):
    """
    Train with EarlyStopping, ModelCheckpoint, and ReduceLROnPlateau.

    patience=15: NFL tabular models converge slowly; patience=5 stops too early.
    200 epochs max: EarlyStopping terminates well before this in practice.
    sample_weight: optional 1-D array (one weight per training sample).
    """
    os.makedirs('artifacts', exist_ok=True)
    os.makedirs('notebooks', exist_ok=True)

    callbacks = [
        keras.callbacks.EarlyStopping(
            monitor='val_loss',
            patience=15,
            restore_best_weights=True,
            verbose=1,
        ),
        keras.callbacks.ModelCheckpoint(
            filepath='artifacts/model_best.keras',
            monitor='val_loss',
            save_best_only=True,
            verbose=1,
        ),
        keras.callbacks.ReduceLROnPlateau(
            monitor='val_loss',
            factor=0.5,
            patience=7,
            min_lr=1e-5,
            verbose=1,
        ),
    ]

    history = model.fit(
        X_train,
        y_train,
        sample_weight=sample_weight,
        validation_data=(X_val, y_val),
        epochs=200,
        batch_size=32,
        callbacks=callbacks,
        verbose=1,
    )

    return history


# ---------------------------------------------------------------------------
# Training curve plot
# ---------------------------------------------------------------------------

def plot_training_history(history):
    """Save training/validation loss and win accuracy curves to notebooks/."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))

    ax1.plot(history.history['loss'],     label='train loss')
    ax1.plot(history.history['val_loss'], label='val loss')
    ax1.set_title('Total Loss')
    ax1.set_xlabel('Epoch')
    ax1.legend()

    # Keras 3 metric names for multi-output models
    win_acc_key     = next((k for k in history.history if 'win' in k and 'accuracy' in k
                            and 'val' not in k), None)
    win_acc_val_key = next((k for k in history.history if 'win' in k and 'accuracy' in k
                            and 'val' in k), None)

    if win_acc_key and win_acc_val_key:
        ax2.plot(history.history[win_acc_key],     label='train win acc')
        ax2.plot(history.history[win_acc_val_key], label='val win acc')
        ax2.axhline(0.63, color='r', linestyle='--', label='63% target')
        ax2.set_title('Win Prediction Accuracy')
        ax2.set_xlabel('Epoch')
        ax2.legend()

    plt.tight_layout()
    plot_path = 'notebooks/training_curves.png'
    plt.savefig(plot_path, dpi=100)
    plt.close()
    print(f"Training curves saved to {plot_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    print("=== Phase 2: Model Training ===\n")

    # Fixed seed so the committed model_best.keras is reproducible. Single-run
    # accuracy has ~1pt variance from random init/shuffling; pinning the seed
    # makes retrains deterministic. Use eval_repeated.py (multi-seed) to judge
    # whether a feature/architecture change is a real gain vs run-to-run noise.
    keras.utils.set_random_seed(42)

    # 1. Load and split data
    (X_train, X_val, X_test,
     y_train, y_val, y_test,
     FEATURES, games, val_games, test_games) = load_data()

    # 2. Scale features
    print()
    X_train_s, X_val_s, X_test_s, scaler = fit_and_save_scaler(X_train, X_val, X_test)

    # 3. Build and compile model
    print()
    model = build_model(n_features=len(FEATURES))
    model = compile_model(model)

    # 4. Train
    print("\nStarting training ...\n")
    history = train_model(model, X_train_s, X_val_s, y_train, y_val)

    # 5. Plot curves
    plot_training_history(history)

    # 6. Evaluate on validation set
    val_passed = evaluate_model(
        model, X_val_s, y_val,
        split_name='Validation',
        games_df=val_games,
    )

    # 7. Evaluate on test set only if validation thresholds passed
    if val_passed:
        evaluate_on_test(model, X_test_s, y_test, games_test=test_games)
    else:
        print("\nSkipping test evaluation — validation thresholds not met.")
        print("Improve the model before evaluating on test set.")

    print("\n=== Phase 2 Complete ===")
