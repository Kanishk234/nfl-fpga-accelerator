"""
Phase 6 (conifer) — end-to-end CHAIN golden for the two-stage wrapper.

Lives in phase6_sim_conifer/ (not phase4_conifer/) per the MLP precedent:
golden generation for the wrapper regression is verification work
(phase6_sim/functional/gen_vectors.py + golden.csv). The per-stage cosim
goldens stay in phase4_conifer/make_tb_data.py — those gate the HLS flow.

The per-stage goldens (make_tb_data.py) feed stage 2 with FLOAT win probs from
xgboost — fine for verifying each IP alone, but the board chains stage 1's
fixed-point margin through a hardware sigmoid into stage 2. This script models
that full chain bit-exactly and emits the golden the phase-5/6 wrapper is
judged against:

    21 feats -> conifer_win (cpp emu, ap_fixed<24,12>) -> margin
             -> sigmoid ROM (spec below)                -> win_prob
    [21 feats, win_prob] -> conifer_spread (cpp emu)    -> residual
    final_spread = residual + vegas_spread              (one adder)

Hardware sigmoid spec (implement the Verilog ROM EXACTLY like this):
  - margin is ap_fixed<24,12>; integer representation m = round(margin * 4096)
  - clamp m to [-32768, 32767]  (i.e. margin in [-8.0, +8.0))
  - index = (m + 32768) >> 6            -> 0..1023  (1024 entries, step 1/64)
  - ROM[index] = round(sigmoid(-8 + (index + 0.5)/64) * 4096), 12-bit unsigned
  - win_prob = ROM[index] / 4096        (ap_fixed<24,12>, always in [0,1))

Run from project root (WSL) after phase4_conifer/make_tb_data.py:
    python phase6_sim_conifer/make_chain_golden.py
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import xgboost as xgb

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import conifer

N_VECTORS = 100          # must match make_tb_data.py
PRECISION = 'ap_fixed<24,12>'
FRAC = 12                # fractional bits -> scale 4096
SCALE = 1 << FRAC
LUT_BITS = 10            # 1024 entries
LUT_SIZE = 1 << LUT_BITS
LUT_RANGE = 8.0          # covers margin in [-8, +8)
OUT_DIR = 'phase6_sim_conifer/chain_golden'


def build_sigmoid_lut():
    """The exact ROM contents the Verilog $readmemh file will hold."""
    idx = np.arange(LUT_SIZE)
    x = -LUT_RANGE + (idx + 0.5) * (2 * LUT_RANGE / LUT_SIZE)  # interval midpoints
    return np.round(1.0 / (1.0 + np.exp(-x)) * SCALE).astype(np.int64)  # 0..4095


def hw_sigmoid(margin, lut):
    """Bit-exact model of the hardware sigmoid path."""
    m = np.round(np.asarray(margin) * SCALE).astype(np.int64)   # fixed-point repr
    m = np.clip(m, -(LUT_SIZE * 32), LUT_SIZE * 32 - 1)         # [-32768, 32767]
    index = (m + LUT_SIZE * 32) >> 6                            # step = 1/64
    return lut[index].astype(np.float64) / SCALE


def cpp_model(model_path, cls, name):
    m = cls()
    m.load_model(model_path)
    cfg = conifer.backends.cpp.auto_config()
    cfg['Precision'] = PRECISION
    cfg['OutputDir'] = f'phase4_conifer/scan_{name}'  # reuse phase-4 compiled scan dir
    c = conifer.converters.convert_from_xgboost(m.get_booster(), cfg)
    c.compile()
    return m, c


def quant(x):
    """Snap to the ap_fixed<24,12> grid (inputs like vegas_spread are exact)."""
    return np.round(np.asarray(x, dtype=np.float64) * SCALE) / SCALE


if __name__ == '__main__':
    print("=== Phase 6 (conifer): end-to-end chain golden ===\n")

    games = pd.read_parquet('data/processed/games.parquet')
    FEAT = json.load(open('artifacts/features.json'))['features']
    vi = FEAT.index('vegas_spread')
    va = games[games['season'].isin([2021, 2022])]
    # Pre-quantize the inputs to the ap_fixed<24,12> grid. The UART protocol
    # sends round(x*4096), so the hardware sees EXACTLY these values; feeding
    # raw floats here would let the C++ emulation truncate (AP_TRN) where the
    # host rounds — a 1-ulp input skew that breaks bit-exactness.
    X = quant(va[FEAT].values.astype('float32'))
    y_win = va['home_win'].values.astype('int32')
    y_spread = va['spread'].values.astype('float32')

    win_f, win_c = cpp_model('artifacts/gbdt/win_model.json', xgb.XGBClassifier, 'win')
    spr_f, spr_c = cpp_model('artifacts/gbdt/spread_model.json', xgb.XGBRegressor, 'spread')

    lut = build_sigmoid_lut()

    # --- full chain over the ENTIRE val set (metrics sanity gate) ---
    print("Running full fixed-point chain over the val set ...")
    margin = np.asarray(win_c.decision_function(X)).squeeze()
    prob = hw_sigmoid(margin, lut)
    X22 = np.column_stack([X, prob]).astype('float64')
    residual = np.asarray(spr_c.decision_function(X22)).squeeze()
    spread = residual + X[:, vi]          # X is already on the fixed grid

    acc = float(np.mean((margin > 0) == y_win))
    mae = float(np.mean(np.abs(spread - y_spread)))
    # reference: per-stage HW numbers with FLOAT win_prob feeding stage 2
    prob_float = win_f.predict_proba(X)[:, 1]
    X22f = np.column_stack([X, prob_float]).astype('float64')
    spread_ref = np.asarray(spr_c.decision_function(X22f)).squeeze() + X[:, vi]
    mae_ref = float(np.mean(np.abs(spread_ref - y_spread)))
    sig_err = float(np.max(np.abs(prob - prob_float)))

    print(f"  win accuracy (chained HW) : {acc:.4f}  (per-stage HW was 0.6538)")
    print(f"  spread MAE   (chained HW) : {mae:.4f}  (float-prob HW was {mae_ref:.4f})")
    print(f"  max |hw_sigmoid - float sigmoid| : {sig_err:.5f}")

    # --- golden vectors for the wrapper testbench (same 100 as make_tb_data) ---
    n = N_VECTORS
    os.makedirs(OUT_DIR, exist_ok=True)
    np.savetxt(f'{OUT_DIR}/tb_input_features.dat', X[:n], fmt='%.6f')
    np.savetxt(f'{OUT_DIR}/golden_margin.dat', margin[:n], fmt='%.8f')
    np.savetxt(f'{OUT_DIR}/golden_win_prob.dat', prob[:n], fmt='%.8f')
    np.savetxt(f'{OUT_DIR}/golden_residual.dat', residual[:n], fmt='%.8f')
    np.savetxt(f'{OUT_DIR}/golden_spread.dat', spread[:n], fmt='%.8f')
    np.save(f'{OUT_DIR}/golden_chain.npy',
            np.column_stack([margin, prob, residual, spread])[:n])

    # XSIM testbench inputs: 24-bit two's-complement hex, game-major, feature 0
    # first — the EXACT fixed-point words the UART protocol carries.
    q = (np.round(X[:n] * SCALE).astype(np.int64) & 0xFFFFFF).reshape(-1)
    with open(f'{OUT_DIR}/tb_inputs.mem', 'w') as f:
        f.write('\n'.join(f'{v:06x}' for v in q) + '\n')

    # Expected outputs as fixed-point words, 2 per game (win_prob, spread) —
    # lets the UART-level testbench self-check bit-exactly.
    gp = np.round(prob[:n] * SCALE).astype(np.int64) & 0xFFFFFF
    gs = np.round(spread[:n] * SCALE).astype(np.int64) & 0xFFFFFF
    with open(f'{OUT_DIR}/golden_fixed.mem', 'w') as f:
        f.write('\n'.join(f'{p:06x}\n{s:06x}' for p, s in zip(gp, gs)) + '\n')

    with open(f'{OUT_DIR}/sigmoid_lut.mem', 'w') as f:
        f.write('\n'.join(f'{v:03x}' for v in lut) + '\n')

    with open(f'{OUT_DIR}/chain_report.json', 'w') as f:
        json.dump({
            'precision': PRECISION,
            'sigmoid_lut': {'entries': LUT_SIZE, 'range': [-LUT_RANGE, LUT_RANGE],
                            'out_bits': 12, 'midpoint_sampled': True},
            'val_metrics': {'win_acc_chained': acc, 'spread_mae_chained': mae,
                            'spread_mae_float_prob': mae_ref,
                            'max_sigmoid_err': sig_err},
            'margin_range': [float(margin.min()), float(margin.max())],
            'n_tb_vectors': n,
        }, f, indent=2)

    print(f"\nWrote {OUT_DIR}/: tb inputs, per-stage + final goldens, "
          f"sigmoid_lut.mem (1024x12), chain_report.json")
    print("Next: phase5_fpga_conifer wrapper HDL, XSIM'd against these goldens.")
