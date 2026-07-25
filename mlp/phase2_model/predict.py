"""
Inference sanity check — loads artifacts and runs predictions on recent games.

Run this after training to verify the full inference pipeline works end-to-end
before handing off to Phase 3 quantization. If predictions look obviously wrong
(e.g. 3% win probability for a heavy home favorite) something is broken in
the features or scaler, not just the model.

Run from project root:
    python mlp/phase2_model/predict.py
"""

import json
import os
import pickle
import sys

import keras
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def run_sample_predictions():
    """Load all artifacts and predict on a handful of 2024 games."""

    # Load artifacts
    model  = keras.models.load_model('artifacts/model_best.keras')
    with open('artifacts/scaler.pkl', 'rb') as f:
        scaler = pickle.load(f)
    with open('artifacts/features.json') as f:
        meta = json.load(f)
    FEATURES = meta['features']

    # Pull a sample from the test set (2024 season)
    games = pd.read_parquet('data/processed/games.parquet')
    sample = games[games['season'] == 2024].head(5)

    if sample.empty:
        print("No 2024 games found in games.parquet")
        return

    X_sample = sample[FEATURES].values.astype('float32')
    X_scaled  = scaler.transform(X_sample)

    preds = model.predict(X_scaled, verbose=0)
    win_probs    = preds[0].flatten()
    spread_preds = preds[1].flatten()

    print("\n=== Sample Predictions (2024 Season) ===\n")
    for i, (_, row) in enumerate(sample.iterrows()):
        win_prob      = win_probs[i]
        spread_pred   = spread_preds[i]
        actual_win    = row['home_win']
        actual_spread = row['spread']
        correct       = 'OK' if (win_prob >= 0.5) == bool(actual_win) else 'WRONG'

        print(f"{row['home_team']} vs {row['away_team']}  "
              f"(Week {row['week']}, {int(row['season'])})")
        print(f"  Win prob:  {win_prob:.1%}  ->  predict {'HOME' if win_prob >= 0.5 else 'AWAY'}  "
              f"|  actual: {'HOME' if actual_win else 'AWAY'}  {correct}")
        print(f"  Spread:    {spread_pred:+.1f} pred  |  actual: {actual_spread:+.0f}")
        print()

    # Range assertions — catch catastrophic model failures before Phase 3
    assert all(0 <= p <= 1 for p in win_probs), \
        f"Win probs outside [0,1]: min={win_probs.min():.3f} max={win_probs.max():.3f}"
    assert all(-60 <= p <= 60 for p in spread_preds), \
        f"Spread predictions outside plausible range: min={spread_preds.min():.1f} max={spread_preds.max():.1f}"

    print("Sample prediction sanity check passed.")


if __name__ == '__main__':
    run_sample_predictions()
