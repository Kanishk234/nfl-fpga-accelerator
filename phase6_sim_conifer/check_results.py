"""
Phase 6 (conifer) — bit-exact check: XSIM chain regression vs chain_golden/.

The gate is EXACT equality of the fixed-point words (no tolerance): the golden
(make_chain_golden.py) models the hardware chain bit-for-bit, so any mismatch
is a real RTL/spec divergence, not noise. Mirrors the role of
phase6_sim/functional/check_results.py in the MLP flow (which needed a +/-3
byte tolerance only because the MLP returns quantized bytes — here the full
24-bit words come back).

Run from project root (WSL) after run_xsim_gbdt.bat:
    python phase6_sim_conifer/check_results.py
"""

import sys

import numpy as np
import pandas as pd

SCALE = 1 << 12
GOLDEN = 'phase6_sim_conifer/chain_golden/golden_chain.npy'
RESULTS = 'phase6_sim_conifer/sim_results.csv'


def to_fixed(x):
    """float on the <24,12> grid -> 24-bit two's-complement integer."""
    return np.round(np.asarray(x) * SCALE).astype(np.int64) & 0xFFFFFF


if __name__ == '__main__':
    golden = np.load(GOLDEN)          # columns: margin, win_prob, residual, spread
    sim = pd.read_csv(RESULTS)
    n = len(sim)
    print(f"=== Phase 6 (conifer) chain regression check: {n} games ===\n")

    g_prob = to_fixed(golden[:n, 1])
    g_spread = to_fixed(golden[:n, 3])
    s_prob = sim['win_prob_fixed'].values.astype(np.int64) & 0xFFFFFF
    s_spread = sim['spread_fixed'].values.astype(np.int64) & 0xFFFFFF

    timeouts = int(sim['timeout'].sum())
    prob_ok = (s_prob == g_prob) & (sim['timeout'].values == 0)
    spread_ok = (s_spread == g_spread) & (sim['timeout'].values == 0)

    print(f"  timeouts             : {timeouts}")
    print(f"  win_prob bit-exact   : {prob_ok.sum()}/{n}")
    print(f"  spread   bit-exact   : {spread_ok.sum()}/{n}")

    if timeouts == 0 and prob_ok.all() and spread_ok.all():
        print("\nPASS — XSIM chain is bit-exact vs the C++ emulation golden.")
        sys.exit(0)

    for i in np.where(~(prob_ok & spread_ok))[0][:10]:
        print(f"  game {i}: sim prob={s_prob[i]:#08x} golden={g_prob[i]:#08x} | "
              f"sim spread={s_spread[i]:#08x} golden={g_spread[i]:#08x} | "
              f"timeout={sim['timeout'][i]}")
    print("\nFAIL — mismatches above (first 10 shown).")
    sys.exit(1)
