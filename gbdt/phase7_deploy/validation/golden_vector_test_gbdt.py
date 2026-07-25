"""
Drive all 100 phase-6 vectors through the board and require the response words
to be BIT-EXACT against the golden.

There is no tolerance band, and that is the point. The MLP version of this test
needed +/-2 counts of slack because the MLP returns quantized bytes; the GBDT
returns full 24-bit ap_fixed<24,12> words, and the board runs the identical RTL
that XSIM simulated against a golden that models the chain bit-for-bit. So the
only correct expected difference is ZERO. Any mismatch is a real defect, not
rounding.

Windows-friendly: needs only pyserial + validation_vectors.json.

    python gbdt/phase7_deploy/validation/golden_vector_test_gbdt.py COM8
    python gbdt/phase7_deploy/validation/golden_vector_test_gbdt.py COM8 --n 20
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from gbdt.phase7_deploy.inference.fpga_client_gbdt import GBDTClient, from_fixed

VECTORS = Path(__file__).resolve().parents[1] / 'validation_vectors.json'


def run(port: str, limit: int | None = None, verbose: bool = True) -> bool:
    data = json.loads(VECTORS.read_text())
    vectors = data['vectors'][:limit] if limit else data['vectors']

    print(f"Running {len(vectors)} golden vectors on the board ({port})")
    print(f"Reference: {data['source']} "
          f"({'XSIM cross-checked' if data.get('sim_checked') else 'golden only'})")
    print("Tolerance: NONE — the board must reproduce the words exactly.\n")

    if verbose:
        print(f"{'#':<4} {'Game':<15} {'Win%':>7} {'Spread':>8} "
              f"{'rawWin':>7} {'golden':>7} {'rawSpr':>8} {'golden':>8} {'':>6}")
        print("-" * 78)

    fails, timeouts, bad_status, latencies = [], 0, 0, []

    with GBDTClient(port) as client:
        for v in vectors:
            i = v['game_idx']
            try:
                r = client.run_inference(v['bytes'])
            except (TimeoutError, RuntimeError) as e:
                timeouts += 1
                fails.append(i)
                print(f"{i:<4} {v['home']:<5} vs {v['away']:<5}  ERROR: {e}")
                continue

            latencies.append(r['latency_ms'])
            ok_status = r['status'] == 'OK'
            if not ok_status:
                bad_status += 1
            exact = (r['raw_win'] == v['exp_win']
                     and r['raw_spread'] == v['exp_spread'])
            passed = exact and ok_status
            if not passed:
                fails.append(i)

            if verbose:
                print(f"{i:<4} {v['home']:<5} vs {v['away']:<5}  "
                      f"{r['win_prob']:>6.1%} {r['spread']:>+8.2f} "
                      f"{r['raw_win']:>7} {v['exp_win']:>7} "
                      f"{r['raw_spread']:>8} {v['exp_spread']:>8} "
                      f"{'ok' if passed else 'FAIL':>6}")

    n = len(vectors)
    passed = n - len(fails)
    print("\n" + "=" * 60)
    print(f"games        : {n}")
    print(f"bit-exact    : {passed}/{n}")
    print(f"timeouts     : {timeouts}")
    print(f"bad status   : {bad_status}")
    if latencies:
        print(f"latency (ms) : min {min(latencies):.1f}  "
              f"median {statistics.median(latencies):.1f}  max {max(latencies):.1f}")

    if fails:
        print(f"\nFAIL — {len(fails)} game(s) mismatched: {fails[:20]}"
              f"{' ...' if len(fails) > 20 else ''}")
        print("A single mismatch means the board is NOT running the verified "
              "design (wrong bitstream?) or the host encoding drifted.")
        return False

    print("\nPASS — the board reproduced all "
          f"{n} golden vectors bit-exactly.")
    return True


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('port', help='COM port e.g. COM8')
    p.add_argument('--n', type=int, default=None, help='run only the first N vectors')
    p.add_argument('--quiet', action='store_true', help='summary only')
    a = p.parse_args()
    sys.exit(0 if run(a.port, a.n, not a.quiet) else 1)
