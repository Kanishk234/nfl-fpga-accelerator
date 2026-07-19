"""
Phase 4 (conifer) — Step 1: precision scan.

Finds the smallest ap_fixed<W,I> that keeps both stages faithful to float:
  win classifier : decision agreement >= 99.9% on val (543 games)
  spread regressor: HW MAE within 0.01 pts of the float MAE

Constraints on the search space:
  - I (integer bits) must cover RAW feature ranges — elo ~1500 needs I >= 12
    (signed). Below that, thresholds saturate and agreement collapses (the
    smoke test measured 96.7% at I=8).
  - W-I (fractional bits) must resolve leaf sums — 132 leaf values of
    magnitude ~0.01-0.3 accumulate in the win head.

Each result is appended to precision_scan.tsv immediately (crash-proof, the
WSL background-run lesson). Run from project root:
    python phase4_conifer/precision_scan.py
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import xgboost as xgb

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import conifer

OUT_TSV = 'phase4_conifer/precision_scan.tsv'

# (W, I) candidates, cheapest first. I=12 -> +/-2048 covers elo ~1500.
CONFIGS = [
    (16, 12), (18, 12), (20, 12), (22, 12), (24, 12),
    (20, 14), (24, 14), (28, 14),
    (24, 16), (32, 16),
]


def load_val():
    games = pd.read_parquet('data/processed/games.parquet')
    FEAT = json.load(open('artifacts/features.json'))['features']
    va = games[games['season'].isin([2021, 2022])]
    X = va[FEAT].values.astype('float32')
    return X, va['home_win'].values.astype('int32'), \
        va['spread'].values.astype('float32'), FEAT


def convert(booster, name, precision):
    cfg = conifer.backends.cpp.auto_config()
    cfg['Precision'] = precision
    cfg['OutputDir'] = f'phase4_conifer/scan_{name}'
    os.makedirs(cfg['OutputDir'], exist_ok=True)
    model = conifer.converters.convert_from_xgboost(booster, cfg)
    model.compile()
    return model


if __name__ == '__main__':
    print("=== Phase 4 (conifer) precision scan ===\n")
    X, y_win, y_spread, FEAT = load_val()

    win = xgb.XGBClassifier()
    win.load_model('artifacts/gbdt/win_model.json')
    spread = xgb.XGBRegressor()
    spread.load_model('artifacts/gbdt/spread_model.json')

    prob_py = win.predict_proba(X)[:, 1]
    dec_py = prob_py > 0.5
    acc_py = float(np.mean(dec_py == y_win))

    X22 = np.column_stack([X, prob_py.astype('float32')]).astype('float32')
    resid_py = spread.predict(X22)
    vegas = X[:, FEAT.index('vegas_spread')]
    mae_py = float(np.mean(np.abs((vegas + resid_py) - y_spread)))
    print(f"float refs: win acc {acc_py:.4f}, spread MAE {mae_py:.4f}\n")

    with open(OUT_TSV, 'w') as f:
        f.write('W\tI\tclf_agree\tclf_acc\treg_absdiff\treg_mae\tpass\n')

    for W, I in CONFIGS:
        prec = f'ap_fixed<{W},{I}>'
        try:
            cm = convert(win.get_booster(), 'win', prec)
            margin_hw = np.asarray(cm.decision_function(X)).squeeze()
            dec_hw = margin_hw > 0
            agree = float(np.mean(dec_hw == dec_py))
            acc_hw = float(np.mean(dec_hw == y_win))

            rm = convert(spread.get_booster(), 'spread', prec)
            resid_hw = np.asarray(rm.decision_function(X22)).squeeze()
            absdiff = float(np.mean(np.abs(resid_hw - resid_py)))
            mae_hw = float(np.mean(np.abs((vegas + resid_hw) - y_spread)))

            ok = agree >= 0.999 and abs(mae_hw - mae_py) <= 0.01
            row = f'{W}\t{I}\t{agree:.4f}\t{acc_hw:.4f}\t{absdiff:.4f}\t{mae_hw:.4f}\t{ok}'
        except Exception as e:
            row = f'{W}\t{I}\tFAIL\t-\t-\t-\t{type(e).__name__}'
        with open(OUT_TSV, 'a') as f:
            f.write(row + '\n')
        print(row)
        sys.stdout.flush()

    print("\nSmallest passing config = the one to lock for HLS synthesis.")
