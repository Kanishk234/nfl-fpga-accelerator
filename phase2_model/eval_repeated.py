"""
Repeated, seeded evaluation harness for comparing feature sets.

Single-run training has ~1-2 point run-to-run variance in win accuracy (random
weight init + batch shuffling, no seed). That swamps the effect size of most
individual features, making single-run before/after comparisons unreliable.

This harness trains each feature set N times with fixed seeds and reports
mean +/- std for win accuracy, AUC, and spread MAE, so feature decisions are
based on a stable central estimate rather than one lucky/unlucky run.

It is read-only with respect to artifacts: it fits its own in-memory scaler per
run and NEVER writes model_best.keras or scaler.pkl. Run from project root:

    python phase2_model/eval_repeated.py
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import keras
from sklearn.preprocessing import MinMaxScaler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phase2_model.model import build_model, compile_model

N_RUNS = 5
SEEDS = list(range(N_RUNS))


def load_splits(feature_list):
    """Load games.parquet and return scaled train/val arrays for the given features."""
    games = pd.read_parquet('data/processed/games.parquet')
    train = games[games['season'] <= 2020]
    val = games[games['season'].isin([2021, 2022])]

    X_train = train[feature_list].values.astype('float32')
    X_val = val[feature_list].values.astype('float32')

    # Fit scaler on train only, per run-set — in memory, never saved.
    scaler = MinMaxScaler(feature_range=(0, 1))
    X_train_s = scaler.fit_transform(X_train)
    X_val_s = scaler.transform(X_val)

    y_train = {'win': train['home_win'].values.astype('float32'),
               'spread': train['spread'].values.astype('float32')}
    y_val = {'win': val['home_win'].values.astype('float32'),
             'spread': val['spread'].values.astype('float32')}
    train_seasons = train['season'].values
    return X_train_s, X_val_s, y_train, y_val, train_seasons


def train_once(X_train_s, X_val_s, y_train, y_val, n_features, seed, sample_weight=None):
    """Train one model with a fixed seed; return (win_acc, auc, spread_mae) on val."""
    keras.utils.set_random_seed(seed)

    model = build_model(n_features=n_features)
    # compile_model prints a summary; suppress by compiling inline with same config.
    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=0.001),
        loss={'win': 'binary_crossentropy', 'spread': keras.losses.Huber(delta=1.0)},
        loss_weights={'win': 1.0, 'spread': 0.15},
        metrics={'win': ['accuracy', 'AUC'], 'spread': ['mae']},
    )

    callbacks = [
        keras.callbacks.EarlyStopping(monitor='val_loss', patience=15,
                                      restore_best_weights=True, verbose=0),
        keras.callbacks.ReduceLROnPlateau(monitor='val_loss', factor=0.5,
                                          patience=7, min_lr=1e-5, verbose=0),
    ]
    model.fit(X_train_s, y_train, sample_weight=sample_weight,
              validation_data=(X_val_s, y_val),
              epochs=200, batch_size=32, callbacks=callbacks, verbose=0)

    win_pred, spread_pred = model.predict(X_val_s, verbose=0)
    win_acc = ((win_pred.flatten() >= 0.5) == y_val['win']).mean()
    auc_metric = keras.metrics.AUC()
    auc_metric.update_state(y_val['win'], win_pred.flatten())
    auc = float(auc_metric.result())
    spread_mae = np.abs(spread_pred.flatten() - y_val['spread']).mean()
    return win_acc, auc, spread_mae


def evaluate_feature_set(name, feature_list, use_recency_weight=False, min_weight=0.25):
    X_train_s, X_val_s, y_train, y_val, train_seasons = load_splits(feature_list)
    if use_recency_weight:
        # Keras 3 multi-output doesn't reliably accept sample_weight — resample instead.
        # Draw training indices with replacement, prob ∝ linear season weight.
        # Resampling with a fixed RNG is reproducible and independent of model seeds.
        s_min, s_max = train_seasons.min(), train_seasons.max()
        norm = (train_seasons - s_min) / (s_max - s_min)
        w = (norm * (1.0 - min_weight) + min_weight).astype('float64')
        probs = w / w.sum()
        rng = np.random.RandomState(0)
        idx = rng.choice(len(X_train_s), size=len(X_train_s), replace=True, p=probs)
        X_train_s = X_train_s[idx]
        y_train = {'win': y_train['win'][idx], 'spread': y_train['spread'][idx]}
    accs, aucs, maes = [], [], []
    for seed in SEEDS:
        acc, auc, mae = train_once(X_train_s, X_val_s, y_train, y_val,
                                   n_features=len(feature_list), seed=seed,
                                   sample_weight=None)
        accs.append(acc); aucs.append(auc); maes.append(mae)
        print(f"  [{name}] seed={seed}: win_acc={acc:.4f}  auc={auc:.4f}  mae={mae:.3f}")

    def stat(xs):
        return float(np.mean(xs)), float(np.std(xs))

    res = {'win_acc': stat(accs), 'auc': stat(aucs), 'mae': stat(maes),
           'win_acc_all': accs, 'auc_all': aucs, 'mae_all': maes}
    print(f"  [{name}] MEAN: win_acc={res['win_acc'][0]:.4f}+/-{res['win_acc'][1]:.4f}  "
          f"auc={res['auc'][0]:.4f}+/-{res['auc'][1]:.4f}  "
          f"mae={res['mae'][0]:.3f}+/-{res['mae'][1]:.3f}\n")
    return res


QB_FEATURES = [
    'home_qb_epa_per_drop', 'away_qb_epa_per_drop',
    'home_qb_cpoe', 'away_qb_cpoe',
]

EPA_FEATURES = [
    'home_off_epa_per_play', 'away_off_epa_per_play',
    'home_def_epa_per_play', 'away_def_epa_per_play',
]


if __name__ == '__main__':
    with open('artifacts/features.json') as f:
        full = json.load(f)['features']

    # Comparison A: 20-feat original vs 21-feat (+vegas_total)
    # Comparison B: (stale — EPA features were reverted)
    # Comparison C: same features, uniform vs recency-weighted (recency proved harmful)
    # Comparison D: 21-feat baseline vs 25-feat (+QB rolling EPA/CPOE)  ← current focus
    import os
    mode = os.environ.get('EVAL_MODE', 'D')

    if mode == 'A':
        baseline_feats = [f for f in full if f not in QB_FEATURES and f != 'vegas_total']
        treatment_feats = [f for f in full if f not in QB_FEATURES]
        base_label = f'baseline-{len(baseline_feats)}'
        treat_label = f'vegas_total-{len(treatment_feats)}'
        base_weight, treat_weight = False, False
    elif mode == 'C':
        # Same features, compare uniform vs recency-weighted training
        baseline_feats = treatment_feats = [f for f in full if f not in QB_FEATURES]
        base_label = f'no-recency-{len(baseline_feats)}'
        treat_label = f'recency-weight-{len(treatment_feats)}'
        base_weight, treat_weight = False, True
    elif mode == 'D':
        # QB features: 21-feat baseline vs 25-feat (+QB rolling EPA/CPOE)
        baseline_feats = [f for f in full if f not in QB_FEATURES]
        treatment_feats = full
        base_label = f'no-qb-{len(baseline_feats)}'
        treat_label = f'with-qb-{len(treatment_feats)}'
        base_weight, treat_weight = False, False
    else:
        # B: stale, same as D now
        baseline_feats = [f for f in full if f not in QB_FEATURES]
        treatment_feats = full
        base_label = f'no-qb-{len(baseline_feats)}'
        treat_label = f'with-qb-{len(treatment_feats)}'
        base_weight, treat_weight = False, False

    print(f"=== Repeated eval (mode={mode}): {N_RUNS} seeds each ===")
    print(f"Baseline:  {len(baseline_feats)} feats  recency_weight={base_weight}  ({base_label})")
    print(f"Treatment: {len(treatment_feats)} feats  recency_weight={treat_weight}  ({treat_label})\n")

    base = evaluate_feature_set(base_label, baseline_feats, use_recency_weight=base_weight)
    treat = evaluate_feature_set(treat_label, treatment_feats, use_recency_weight=treat_weight)

    d_acc = treat['win_acc'][0] - base['win_acc'][0]
    d_auc = treat['auc'][0] - base['auc'][0]
    d_mae = treat['mae'][0] - base['mae'][0]
    print("=== DELTA (treatment - baseline, means) ===")
    print(f"  win_acc: {d_acc:+.4f}   (baseline std {base['win_acc'][1]:.4f})")
    print(f"  auc:     {d_auc:+.4f}   (baseline std {base['auc'][1]:.4f})")
    print(f"  mae:     {d_mae:+.3f}    (baseline std {base['mae'][1]:.3f})")
    print("\nRule of thumb: a delta smaller than the baseline std is likely noise.")
