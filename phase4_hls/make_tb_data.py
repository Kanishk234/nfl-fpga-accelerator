"""
Generate cosim/csim test vectors for the HLS project.

WHY this exists (see AUDIT_REPORT.md §1): the io_stream regeneration must be
verified with `cosim_design -rtl verilog`, which runs the REAL RTL with the REAL
FIFOs against this testbench. Cosim is the gate that catches the stream-deadlock
class of bug that C-sim and the Phase-6 stubbed sim both missed. But the hls4ml
testbench reads tb_data/tb_input_features.dat + tb_output_predictions.dat, and
those are not emitted by convert.py — without them cosim falls back to a few zero
vectors and proves almost nothing. This writes real validation vectors so cosim
exercises non-trivial data end to end.

Format (matches myproject_test.cpp):
  tb_input_features.dat   : one line per sample, 21 space-separated scaled floats
  tb_output_predictions.dat: one line per sample, "<win> <spread>"

Inputs are the scaled [0,1] features (exactly what C-sim fed hls_model.predict),
so csim/cosim/Verilog deltas stay comparable. Run from project root:
    source venv/bin/activate
    python phase4_hls/make_tb_data.py
"""

import os
import sys
import json
import pickle

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phase4_hls.convert import load_inference_model

N = 20  # cosim runs an RTL sim per vector — keep small; 20 covers a range without hours of sim


def main():
    out_dir = 'phase4_hls/hls_project/tb_data'
    os.makedirs(out_dir, exist_ok=True)

    model, features = load_inference_model()

    scaler = pickle.load(open('artifacts/scaler.pkl', 'rb'))
    with open('artifacts/features.json') as f:
        meta = json.load(f)
    games = pd.read_parquet('data/processed/games.parquet')
    val   = games[games['season'].isin([2021, 2022])]
    X_val = scaler.transform(val[meta['features']].values.astype('float32'))

    X = np.ascontiguousarray(X_val[:N])
    preds = model.predict(X, verbose=0)
    win    = np.asarray(preds[0]).flatten()
    spread = np.asarray(preds[1]).flatten()

    in_path  = os.path.join(out_dir, 'tb_input_features.dat')
    out_path = os.path.join(out_dir, 'tb_output_predictions.dat')

    with open(in_path, 'w') as f:
        for row in X:
            f.write(' '.join(f'{v:.8f}' for v in row) + '\n')

    with open(out_path, 'w') as f:
        for w, s in zip(win, spread):
            f.write(f'{w:.8f} {s:.8f}\n')

    print(f"Wrote {N} vectors:")
    print(f"  {in_path}  (21 features/line)")
    print(f"  {out_path} (win spread/line)")
    print("Now run synthesis + cosim on Windows (run_synthesis.bat).")


if __name__ == '__main__':
    main()
