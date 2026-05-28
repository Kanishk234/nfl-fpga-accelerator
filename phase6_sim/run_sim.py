# phase6_sim/run_sim.py
# Run from project root: python phase6_sim/run_sim.py

import sys, os
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

def load_test_vectors(n_games=50):
    """
    Step 1: Load 50 games from the 2023-2024 test set, run through
    the quantized model, write CSVs for cocotb and for the report.
    """
    import tensorflow as tf
    tf.config.run_functions_eagerly(True)
    import json, pickle, numpy as np, pandas as pd, csv, functools
    from phase3_quantization.qkeras_model import (
        build_quantized_model, compile_quantized_model
    )

    meta     = json.load(open('artifacts/features.json'))
    FEATURES = meta['features']
    scaler   = pickle.load(open('artifacts/scaler.pkl', 'rb'))
    games    = pd.read_parquet('data/processed/games.parquet')

    # Use 2023-2024 test set — held-out, never seen by model
    test_games = games[games['season'].isin([2023, 2024])].head(n_games)
    test_games = test_games.reset_index(drop=True)

    model = build_quantized_model(n_features=len(FEATURES))
    compile_quantized_model(model)
    model.load_weights('artifacts/model_quantized.keras')

    X_float = scaler.transform(test_games[FEATURES].values.astype('float32'))
    preds   = model.predict(X_float, verbose=0)
    win_probs = preds[0].flatten()
    spreads   = preds[1].flatten()

    # Write input_games.csv
    with open('phase6_sim/test_vectors/input_games.csv', 'w', newline='') as f:
        cols = ['home_team', 'away_team', 'season', 'week'] + \
               [f'feature_{i}' for i in range(21)] + ['checksum']
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for i in range(n_games):
            feat_uint8 = np.clip(
                np.round(X_float[i] * 255), 0, 255
            ).astype(int).tolist()
            checksum = functools.reduce(lambda a, b: a ^ b, feat_uint8)
            row = {
                'home_team': test_games.iloc[i].get('home_team', ''),
                'away_team': test_games.iloc[i].get('away_team', ''),
                'season':    int(test_games.iloc[i]['season']),
                'week':      int(test_games.iloc[i]['week']),
                'checksum':  checksum,
            }
            for j, b in enumerate(feat_uint8):
                row[f'feature_{j}'] = b
            writer.writerow(row)

    # Write expected_outputs.csv
    with open('phase6_sim/test_vectors/expected_outputs.csv', 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'game_idx', 'home_team', 'away_team', 'win_prob_float',
            'spread_float', 'win_uint8', 'spread_int8'
        ])
        writer.writeheader()
        for i in range(n_games):
            win_u8  = int(np.clip(round(win_probs[i] * 256), 0, 255))
            sprd_i8 = int(np.clip(round(spreads[i]), -128, 127))
            writer.writerow({
                'game_idx':       i,
                'home_team':      test_games.iloc[i].get('home_team', ''),
                'away_team':      test_games.iloc[i].get('away_team', ''),
                'win_prob_float': float(win_probs[i]),
                'spread_float':   float(spreads[i]),
                'win_uint8':      win_u8,
                'spread_int8':    sprd_i8,
            })

    # Write game_metadata.csv (human-readable, named features)
    with open('phase6_sim/test_vectors/game_metadata.csv', 'w', newline='') as f:
        meta_cols = ['game_idx', 'home_team', 'away_team', 'season', 'week',
                     'py_win_prob', 'py_spread'] + FEATURES
        writer = csv.DictWriter(f, fieldnames=meta_cols)
        writer.writeheader()
        for i in range(n_games):
            row = {
                'game_idx':    i,
                'home_team':   test_games.iloc[i].get('home_team', ''),
                'away_team':   test_games.iloc[i].get('away_team', ''),
                'season':      int(test_games.iloc[i]['season']),
                'week':        int(test_games.iloc[i]['week']),
                'py_win_prob': f"{win_probs[i]:.3f}",
                'py_spread':   f"{spreads[i]:+.1f}",
            }
            for feat in FEATURES:
                row[feat] = f"{float(test_games.iloc[i][feat]):.4f}"
            writer.writerow(row)

    print(f"Generated {n_games} test vectors")
    print(f"  input_games.csv, expected_outputs.csv, game_metadata.csv")
    print(f"\nSample predictions:")
    print(f"{'#':<3} {'Home':<5} vs {'Away':<5} {'Wk':>3}  {'Win%':>6}  {'Spread':>7}")
    print("-" * 40)
    for i in range(min(5, n_games)):
        row = test_games.iloc[i]
        print(f"{i:<3} {row.get('home_team','?'):<5} vs "
              f"{row.get('away_team','?'):<5} W{int(row['week']):>2}  "
              f"{win_probs[i]:>6.1%}  {spreads[i]:>+7.1f}")
    return n_games


def merge_and_report():
    """
    Step 3 (post-simulation): merge sim_outputs.csv with game_metadata.csv
    to produce sim_report.csv — the main human-readable output.
    """
    import csv, numpy as np

    outputs  = {int(r['game_idx']): r
                for r in csv.DictReader(
                    open('phase6_sim/test_vectors/sim_outputs.csv')
                )}
    metadata = {int(r['game_idx']): r
                for r in csv.DictReader(
                    open('phase6_sim/test_vectors/game_metadata.csv')
                )}
    expected = {int(r['game_idx']): r
                for r in csv.DictReader(
                    open('phase6_sim/test_vectors/expected_outputs.csv')
                )}

    report_rows = []
    for idx in sorted(outputs.keys()):
        o, m, e = outputs[idx], metadata[idx], expected[idx]
        hw_win  = float(o['hw_win'])
        hw_sprd = int(o['hw_spread'])
        py_win  = float(e['win_prob_float'])
        py_sprd = float(e['spread_float'])

        report_rows.append({
            'game_idx':              idx,
            'home_team':             m['home_team'],
            'away_team':             m['away_team'],
            'season':                m['season'],
            'week':                  m['week'],
            'py_win_prob':           f"{py_win:.3f}",
            'py_predicted_winner':   m['home_team'] if py_win >= 0.5 else m['away_team'],
            'py_spread_call':        f"{py_sprd:+.1f}",
            'fpga_win_prob':         f"{hw_win:.3f}",
            'fpga_predicted_winner': m['home_team'] if hw_win >= 0.5 else m['away_team'],
            'fpga_spread_call':      f"{hw_sprd:+d}",
            'models_agree':          'YES' if (hw_win >= 0.5) == (py_win >= 0.5) else 'NO',
            'fpga_status':           'OK' if o['status_ok'] == 'True' else 'ERR',
            'win_delta_counts':      o['win_delta'],
            'spread_delta_pts':      o['spread_delta'],
        })

    with open('phase6_sim/test_vectors/sim_report.csv', 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=report_rows[0].keys())
        writer.writeheader()
        writer.writerows(report_rows)

    # Summary stats
    agree_count = sum(1 for r in report_rows if r['models_agree'] == 'YES')
    ok_count    = sum(1 for r in report_rows if r['fpga_status'] == 'OK')
    w_deltas    = [int(r['win_delta_counts']) for r in report_rows]
    s_deltas    = [int(r['spread_delta_pts'])  for r in report_rows]

    print(f"\n{'='*50}")
    print(f"sim_report.csv written: {len(report_rows)} games")
    print(f"Win agreement:   {agree_count}/{len(report_rows)} "
          f"({agree_count/len(report_rows):.1%})")
    print(f"Status OK:       {ok_count}/{len(report_rows)}")
    print(f"Avg win delta:   {np.mean(w_deltas):.2f} counts")
    print(f"Spread MAE:      {np.mean(s_deltas):.2f} pts")
    print(f"{'='*50}")


if __name__ == '__main__':
    import subprocess, sys
    print("=== Phase 6 Simulation Pipeline ===\n")

    # Step 1: Generate vectors
    print("Step 1: Generating test vectors...")
    load_test_vectors(50)

    # Step 2: Run regression simulation
    print("\nStep 2: Running 50-game regression simulation...")
    result = subprocess.run(
        ['make', '-C', 'phase6_sim/cocotb', 'test_regression'],
        capture_output=False   # stream output live
    )
    if result.returncode != 0:
        print("\nRegression simulation FAILED")
        sys.exit(1)

    # Step 3: Merge and report
    print("\nStep 3: Generating sim_report.csv...")
    merge_and_report()
    print("\nDone. Review phase6_sim/test_vectors/sim_report.csv")
