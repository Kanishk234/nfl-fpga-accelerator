"""
Phase 4 (conifer) — Step 2b: generate testbench vectors for csim/cosim.

Writes tb_data/tb_input_features.dat for both HLS projects (win: 21 features,
spread: 22 = 21 + win_prob), plus a golden_cpp.npy per project holding the
bit-accurate C++ emulation outputs — the verification golden for later stages
(NOT float xgboost; see PHASE4_CONIFER_COMPLETE.md Step 1).

100 val-set vectors: enough for a meaningful cosim without hour-long RTL sim.

Run from project root (WSL) after convert_hls.py:
    python gbdt/phase4_hls/make_tb_data.py
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import xgboost as xgb

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import conifer

N_VECTORS = 100
PRECISION = 'ap_fixed<24,12>'  # must match convert_hls.py


def cpp_golden(booster, name, X):
    """Bit-accurate C++ emulation output at the locked precision."""
    cfg = conifer.backends.cpp.auto_config()
    cfg['Precision'] = PRECISION
    cfg['OutputDir'] = f'gbdt/phase4_hls/scan_{name}'  # reuse scan dir
    m = conifer.converters.convert_from_xgboost(booster, cfg)
    m.compile()
    return np.asarray(m.decision_function(X)).squeeze()


def write_tb(project, X):
    path = f'gbdt/phase4_hls/{project}/tb_data/tb_input_features.dat'
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.savetxt(path, X, fmt='%.6f')
    print(f"  {path}: {X.shape[0]} vectors x {X.shape[1]} features")


if __name__ == '__main__':
    print("=== Phase 4 (conifer) testbench data generation ===\n")

    games = pd.read_parquet('data/processed/games.parquet')
    FEAT = json.load(open('artifacts/features.json'))['features']
    va = games[games['season'].isin([2021, 2022])]
    X = va[FEAT].values.astype('float32')[:N_VECTORS]

    win = xgb.XGBClassifier()
    win.load_model('artifacts/gbdt/win_model.json')
    spread = xgb.XGBRegressor()
    spread.load_model('artifacts/gbdt/spread_model.json')

    # win project: 21 raw features
    write_tb('hls_win', X)
    g = cpp_golden(win.get_booster(), 'win', X)
    np.save('gbdt/phase4_hls/hls_win/golden_cpp.npy', g)
    print(f"  golden_cpp.npy: margin range [{g.min():.3f}, {g.max():.3f}]")

    # spread project: 21 features + float win prob (matches training-time stack)
    win_prob = win.predict_proba(X)[:, 1].astype('float32')
    X22 = np.column_stack([X, win_prob]).astype('float32')
    write_tb('hls_spread', X22)
    g = cpp_golden(spread.get_booster(), 'spread', X22)
    np.save('gbdt/phase4_hls/hls_spread/golden_cpp.npy', g)
    print(f"  golden_cpp.npy: residual range [{g.min():.3f}, {g.max():.3f}]")

    print("\nNext: gbdt/phase4_hls/run_synthesis.bat (Windows)")
