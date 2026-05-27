#!/usr/bin/env python3
"""Generate golden reference vectors from the quantized model for Phase 6 verification."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import json
import pickle
import numpy as np
import pandas as pd

import tensorflow as tf
tf.config.run_functions_eagerly(True)

from phase3_quantization.qkeras_model import build_quantized_model, compile_quantized_model


def generate_vectors(n_games=10):
    meta     = json.load(open('artifacts/features.json'))
    FEATURES = meta['features']
    scaler   = pickle.load(open('artifacts/scaler.pkl', 'rb'))
    games    = pd.read_parquet('data/processed/games.parquet')

    model = build_quantized_model(n_features=len(FEATURES))
    compile_quantized_model(model)
    model.load_weights('artifacts/model_quantized.keras')

    sample = games[games['season'] == 2024].head(n_games).reset_index(drop=True)

    X_float = scaler.transform(sample[FEATURES].values.astype('float32'))
    X_uint8 = np.round(X_float * 255).astype(np.uint8)

    preds = model.predict(X_float, verbose=0)
    win_probs_float = preds[0].flatten()
    spreads_float   = preds[1].flatten()

    win_uint8   = np.clip(np.round(win_probs_float * 256), 0, 255).astype(np.uint8)
    spread_int8 = np.clip(np.round(spreads_float), -128, 127).astype(np.int8)

    vectors = []
    for i in range(n_games):
        checksum = 0
        for b in X_uint8[i]:
            checksum ^= int(b)

        vectors.append({
            'game_index':           i,
            'home_team':            str(sample.iloc[i].get('home_team', 'UNK')),
            'away_team':            str(sample.iloc[i].get('away_team', 'UNK')),
            'season':               int(sample.iloc[i]['season']),
            'week':                 int(sample.iloc[i]['week']),
            'features_float':       X_float[i].tolist(),
            'features_uint8':       X_uint8[i].tolist(),
            'checksum':             int(checksum),
            'win_prob_float':       float(win_probs_float[i]),
            'spread_float':         float(spreads_float[i]),
            'win_uint8_expected':   int(win_uint8[i]),
            'spread_int8_expected': int(spread_int8[i]),
            'win_uint8_tolerance':  13,
            'spread_tolerance_pts': 2,
        })

    os.makedirs('phase6_sim/reference', exist_ok=True)
    with open('phase6_sim/reference/golden_vectors.json', 'w') as f:
        json.dump(vectors, f, indent=2)

    print(f"Generated {n_games} golden vectors")
    for v in vectors:
        print(f"  Game {v['game_index']}: {v['home_team']} vs {v['away_team']}"
              f" W{v['week']} — win={v['win_prob_float']:.1%}, spread={v['spread_float']:+.1f}")

    return vectors


if __name__ == '__main__':
    generate_vectors()
