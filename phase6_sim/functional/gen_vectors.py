"""
Generate functional-test vectors for the REAL io_stream IP simulation.

This is the honest Phase 6 verification the audit called for (§1, §6.2, §6.3):
the old regression drove a STUB and computed its golden from un-quantized inputs.
Here we:
  - quantize features exactly as the hardware does (byte b -> b/256), and compute
    the golden win/spread from the SAME inference model the IP was built from
    (convert.load_inference_model — snapped fixed-point weights), so the golden
    isolates hardware arithmetic error instead of bundling input quantization in.
  - emit the feature bytes as a flat $readmemh file the Verilog testbench reads.

Outputs (in phase6_sim/functional/):
  tb_inputs.mem   : N*21 feature bytes, hex, whitespace-separated (one game per line)
  golden.csv      : game_idx, home, away, win_prob, spread, win_byte, spread_byte

Run from project root:
    source venv/bin/activate
    python phase6_sim/functional/gen_vectors.py
"""
import os, sys, json, pickle, csv
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from phase4_hls.convert import load_inference_model

N = 50
OUT = 'phase6_sim/functional'


def main():
    os.makedirs(OUT, exist_ok=True)
    model, features = load_inference_model()       # snapped-weight float model = the IP's reference

    scaler = pickle.load(open('artifacts/scaler.pkl', 'rb'))
    meta   = json.load(open('artifacts/features.json'))
    games  = pd.read_parquet('data/processed/games.parquet')
    test   = games[games['season'].isin([2023, 2024])].head(N).reset_index(drop=True)

    X_float  = scaler.transform(test[meta['features']].values.astype('float32'))
    feat_u8  = np.clip(np.round(X_float * 255), 0, 255).astype(int)     # exactly what the host sends
    X_hw     = feat_u8.astype('float32') / 256.0                        # exactly what the FPGA computes on
    preds    = model.predict(X_hw, verbose=0)
    win      = np.asarray(preds[0]).flatten()
    spread   = np.asarray(preds[1]).flatten()

    # Expected response bytes, matching mlp_controller's decode:
    #   win byte   = top 8 fractional bits of ap_fixed<18,6> ~= floor(win*256), saturated to [0,255]
    #   spread byte= integer byte [23:16] of ap_fixed<32,16> ~= floor(spread), as signed int8
    win_byte    = np.clip(np.floor(win * 256), 0, 255).astype(int)
    spread_byte = np.clip(np.floor(spread), -128, 127).astype(int)

    with open(os.path.join(OUT, 'tb_inputs.mem'), 'w') as f:
        for i in range(N):
            f.write(' '.join(f'{b:02x}' for b in feat_u8[i]) + '\n')

    with open(os.path.join(OUT, 'golden.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['game_idx', 'home', 'away', 'win_prob', 'spread', 'win_byte', 'spread_byte'])
        for i in range(N):
            w.writerow([i, test.iloc[i].get('home_team', ''), test.iloc[i].get('away_team', ''),
                        f'{win[i]:.6f}', f'{spread[i]:.6f}', int(win_byte[i]), int(spread_byte[i])])

    print(f"Wrote {N} vectors to {OUT}/tb_inputs.mem and golden.csv")
    print(f"Sample: game0 win={win[0]:.3f} (byte {int(win_byte[0])}) spread={spread[0]:+.2f} (byte {int(spread_byte[0])})")


if __name__ == '__main__':
    main()
