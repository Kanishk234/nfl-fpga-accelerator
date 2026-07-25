"""
Phase 4 (conifer) — smoke test.

Purpose: de-risk the GBDT->FPGA path BEFORE building the full flow. The two
questions this must answer:

  1. Does conifer convert our XGBoost *classifier* (win head)?    [expected: yes]
  2. Does conifer convert our XGBoost *regressor* (spread head)?  [the risk —
     conifer grew out of HEP classification; regression support is the open
     question that decides this phase's design]

Strategy: convert both saved models with the C++ backend (bit-accurate emulation
of the HLS fixed-point arithmetic, no Vivado needed — runs in WSL). If both
convert and the emulated outputs track the float XGBoost predictions, the
full Vivado HLS synthesis on Windows is unblocked.

NOTE on stage-2 inputs: the spread model takes 22 inputs (21 features +
win_prob). In hardware the two stages chain: stage1 output feeds stage2 input.
The smoke test evaluates each stage standalone, using Python-computed win probs
for stage 2 — chaining is a phase-5 wrapper concern, not a conversion concern.

NOTE on outputs: conifer emits the raw ensemble sum (margin/logit for the
classifier — no sigmoid; raw residual for the regressor). The sigmoid and the
"+ vegas_spread" adder live in the wrapper, exactly like the MLP's hls4ml
sigmoid lives in its generated IP. Accuracy comparisons here are done on the
margin, which is monotone in probability — decision-identical at threshold 0.

Run from project root (WSL):
    python phase4_conifer/smoke_test.py
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import xgboost as xgb

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import conifer

# Features are RAW (no scaler — trees are scale-invariant), so elo values
# ~1500 must be representable: 16 integer bits. Width 32 is generous for a
# smoke test; the full flow will tune this down per-feature.
PRECISION = 'ap_fixed<32,16>'


# NOTE: conifer 1.9 handles xgboost >= 2's bracketed base_score ('[-0.27]')
# correctly in its converter — no model patching needed. (An earlier version of
# this test crashed on that string, but the bare float() was in OUR check code,
# not in conifer.)


def load_val():
    games = pd.read_parquet('data/processed/games.parquet')
    FEAT = json.load(open('artifacts/features.json'))['features']
    va = games[games['season'].isin([2021, 2022])]
    X = va[FEAT].values.astype('float32')
    y_win = va['home_win'].values.astype('int32')
    y_spread = va['spread'].values.astype('float32')
    return X, y_win, y_spread, FEAT


def convert(booster, name, cfg_precision=PRECISION):
    """Convert one XGBoost model with the cpp backend; return conifer model."""
    cfg = conifer.backends.cpp.auto_config()
    cfg['Precision'] = cfg_precision
    cfg['OutputDir'] = f'phase4_conifer/smoke_{name}'
    os.makedirs(cfg['OutputDir'], exist_ok=True)
    model = conifer.converters.convert_from_xgboost(booster, cfg)
    model.compile()
    return model


if __name__ == '__main__':
    print("=== Phase 4 (conifer) smoke test ===")
    print(f"conifer {conifer.__version__} / xgboost {xgb.__version__}\n")

    X, y_win, y_spread, FEAT = load_val()

    # ---------------- Stage 1: win classifier ----------------
    print("[1/2] Converting win classifier (the expected-easy one) ...")
    win = xgb.XGBClassifier()
    win.load_model('artifacts/gbdt/win_model.json')

    ok_clf = False
    try:
        cm = convert(win.get_booster(), 'win')
        margin_hw = np.asarray(cm.decision_function(X)).squeeze()
        margin_py = win.predict_proba(X)[:, 1]  # prob; compare via decisions
        # decisions: margin>0  <=>  prob>0.5 (sigmoid is monotone)
        agree = float(np.mean((margin_hw > 0) == (margin_py > 0.5)))
        acc_hw = float(np.mean((margin_hw > 0) == y_win))
        print(f"  converted + compiled OK")
        print(f"  decision agreement HW vs float: {agree:.4f} (want ~1.0)")
        print(f"  HW val accuracy: {acc_hw:.4f} (float: "
              f"{float(np.mean((margin_py > 0.5) == y_win)):.4f})")
        ok_clf = agree > 0.99
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}")

    # ---------------- Stage 2: spread residual regressor ----------------
    print("\n[2/2] Converting spread residual regressor (the risk) ...")
    spread = xgb.XGBRegressor()
    spread.load_model('artifacts/gbdt/spread_model.json')

    ok_reg = False
    try:
        # stage-2 input = 21 features + win prob (Python-computed for this test)
        win_prob = win.predict_proba(X)[:, 1].astype('float32')
        X22 = np.column_stack([X, win_prob]).astype('float32')

        rm = convert(spread.get_booster(), 'spread')
        resid_hw = np.asarray(rm.decision_function(X22)).squeeze()
        resid_py = spread.predict(X22)
        # conifer regression output may need base_score offset — check both
        diff_raw = float(np.mean(np.abs(resid_hw - resid_py)))
        # note: xgboost >= 2 brackets base_score ('[-0.27]') — strip before float
        base = float(json.loads(
            spread.get_booster().save_config()
        )['learner']['learner_model_param']['base_score'].strip('[]'))
        diff_base = float(np.mean(np.abs((resid_hw + base) - resid_py)))
        offset = base if diff_base < diff_raw else 0.0
        diff = min(diff_raw, diff_base)
        vegas = X[:, FEAT.index('vegas_spread')]
        mae_hw = float(np.mean(np.abs((vegas + resid_hw + offset) - y_spread)))
        mae_py = float(np.mean(np.abs((vegas + resid_py) - y_spread)))
        print(f"  converted + compiled OK")
        print(f"  mean |HW - float| residual: {diff:.4f} pts "
              f"(base_score offset applied: {offset:.3f})")
        print(f"  HW spread MAE: {mae_hw:.4f} (float: {mae_py:.4f})")
        ok_reg = diff < 0.5
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}")

    # ---------------- Verdict ----------------
    print("\n=== Verdict ===")
    print(f"  classifier path: {'OK' if ok_clf else 'BLOCKED'}")
    print(f"  regressor  path: {'OK' if ok_reg else 'BLOCKED'}")
    if ok_clf and ok_reg:
        print("  Full conifer flow is UNBLOCKED — proceed to precision tuning "
              "+ Vivado HLS synthesis.")
    elif ok_clf:
        print("  Regression path blocked — fall back plan: bin spread into "
              "buckets (ordinal classification) or emit residual via classifier "
              "ensemble trick. Investigate before building the full flow.")
    else:
        print("  Conversion fundamentally blocked — investigate conifer "
              "version/API before anything else.")
