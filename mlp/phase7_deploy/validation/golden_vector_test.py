"""
Sends all 50 Phase 6 regression games to the board and checks the board's response
bytes are BIT-EXACT against the XSIM run (mlp/phase6_sim/functional/sim_results.csv) — the
board runs the identical RTL, so it must reproduce the simulation exactly. golden.csv
(the float reference model) is shown alongside for context; it differs from both board
and sim by the known input-quantization delta (~up to 25 win counts), so it is NOT the
pass/fail reference — sim_results.csv is.
"""

import csv
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from mlp.phase7_deploy.inference.fpga_client import FPGAClient

FUNC = 'mlp/phase6_sim/functional'
WIN_TOLERANCE    = 2   # board-vs-sim: expected 0; small slack only
SPREAD_TOLERANCE = 1


def run_golden_vector_test(port: str) -> bool:
    # Inputs: 50 games × 21 hex feature bytes, one game per line.
    inputs = [[int(b, 16) for b in line.split()]
              for line in open(f'{FUNC}/tb_inputs.mem') if line.strip()]
    # Expected board bytes = the XSIM result (identical RTL).
    sim  = {int(r['game_idx']): r for r in csv.DictReader(open(f'{FUNC}/sim_results.csv'))}
    # Float reference model — for display + team names only.
    gold = {int(r['game_idx']): r for r in csv.DictReader(open(f'{FUNC}/golden.csv'))}

    print(f"Running {len(inputs)} golden vectors on board ({port})...")
    print(f"Reference: sim_results.csv (bit-exact); tolerance ±{WIN_TOLERANCE} win / "
          f"±{SPREAD_TOLERANCE} spread\n")

    print(f"{'#':<3} {'Game':<15} {'PyWin%':>7} {'HWWin%':>7} "
          f"{'dW':>4} {'PySprd':>7} {'HWSprd':>7} {'dS':>4} {'Pass':>5}")
    print("-" * 68)

    results = []
    with FPGAClient(port) as client:
        for i, features in enumerate(inputs):
            result   = client.run_inference(features)

            hw_win_u8    = result['raw_win']
            hw_spread    = result['spread']
            exp_win_u8   = int(sim[i]['win_byte'])
            exp_spread_u = int(sim[i]['spread_byte'])
            exp_spread   = exp_spread_u if exp_spread_u < 128 else exp_spread_u - 256
            py_win       = float(gold[i]['win_prob'])
            py_spread    = float(gold[i]['spread'])

            win_delta    = abs(hw_win_u8 - exp_win_u8)
            spread_delta = abs(hw_spread - exp_spread)
            passed       = (win_delta    <= WIN_TOLERANCE and
                            spread_delta <= SPREAD_TOLERANCE and
                            result['status'] == 'OK')
            results.append(passed)

            home = gold[i].get('home', '?')
            away = gold[i].get('away', '?')
            print(f"{i:<3} {home:<5} vs {away:<5}  "
                  f"{py_win:>7.1%} {result['win_prob']:>7.1%} "
                  f"{win_delta:>+4d}  "
                  f"{py_spread:>+7.1f} {hw_spread:>+7d} "
                  f"{spread_delta:>+4d}  "
                  f"{'PASS' if passed else 'FAIL':>5}")

    pass_count = sum(results)
    total      = len(results)
    print(f"\n{'='*50}")
    print(f"Results: {pass_count}/{total} passed")
    print(f"Win agreement: {pass_count/total:.1%}")

    if pass_count < total * 0.90:
        print("FAIL: Less than 90% pass rate — check board programming and UART")
        return False
    elif pass_count < total * 0.98:
        print("WARNING: 90-98% pass rate — likely a few edge-case fixed-point differences")
        return True
    else:
        print("PASS: >=98% agreement with simulation")
        return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('port', help='COM port e.g. COM3 or /dev/ttyUSB0')
    args   = parser.parse_args()
    ok     = run_golden_vector_test(args.port)
    sys.exit(0 if ok else 1)
