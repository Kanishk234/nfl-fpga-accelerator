"""
Compare the RTL functional-sim results against the Python golden.

Reads:
  mlp/phase6_sim/functional/golden.csv       (game_idx, home, away, win_prob, spread, win_byte, spread_byte)
  mlp/phase6_sim/functional/sim_results.csv   (game_idx, win_byte, spread_byte, timeout)   <- from XSIM/iverilog

Reports winner agreement, win-byte delta (counts), spread delta (points), timeouts,
and an overall PASS/FAIL against tolerances. The win/spread bytes are produced by the
REAL mlp_controller + REAL myproject IP, so this validates the whole hardware datapath
(controller packing + fixed-point MLP + controller decode) against the model.

Run from project root:
    python mlp/phase6_sim/functional/check_results.py [sim_results.csv]
"""
import sys, csv

FUNC = 'mlp/phase6_sim/functional'
# Tolerances. fixed<18,6> C-sim error was mean 0.047 / max 0.096 in probability
# (~12 / ~25 counts). Spread is floored vs rounded -> allow a couple points.
WIN_MAX_TOL    = 26     # counts (~0.10 prob)
WIN_MEAN_TOL   = 13     # counts (~0.05 prob)
SPREAD_MAX_TOL = 3      # points
# A quantized accelerator cannot be expected to match the FLOAT model's discrete
# home/away label on games the model itself scores near 0.5 — the ~0.05 fixed-point
# error tips the call. Only a winner flip on a CONFIDENT game (model prob far from
# 0.5) indicates a real datapath bug. Games within this band are excused.
COINFLIP_BAND  = 0.05   # |model win_prob - 0.5|


def s8(b):
    b = int(b)
    return b - 256 if b > 127 else b


def main():
    sim_path = sys.argv[1] if len(sys.argv) > 1 else f'{FUNC}/sim_results.csv'
    golden = {int(r['game_idx']): r for r in csv.DictReader(open(f'{FUNC}/golden.csv'))}
    sim    = {int(r['game_idx']): r for r in csv.DictReader(open(sim_path))}

    n = 0; agree = 0; timeouts = 0
    confident = 0; confident_flips = []   # winner disagreements on non-coinflip games = real bugs
    coinflip_flips = 0                     # disagreements on near-0.5 games = excused
    win_deltas = []; spread_deltas = []
    worst = []   # (win_delta, idx, detail)

    for idx in sorted(golden):
        if idx not in sim:
            print(f"  game {idx}: MISSING from sim results"); continue
        g, s = golden[idx], sim[idx]
        n += 1
        if int(s['timeout']):
            timeouts += 1
            print(f"  game {idx}: TIMEOUT (no result from IP)")
            continue
        gw_prob = float(g['win_prob'])
        gw, sw = int(g['win_byte']), int(s['win_byte'])
        gs, ss = int(g['spread_byte']), s8(s['spread_byte'])
        wd = abs(sw - gw); sd = abs(ss - gs)
        win_deltas.append(wd); spread_deltas.append(sd)
        same_winner = (sw >= 128) == (gw_prob >= 0.5)
        if same_winner:
            agree += 1
        is_confident = abs(gw_prob - 0.5) >= COINFLIP_BAND
        if is_confident:
            confident += 1
            if not same_winner:
                confident_flips.append((idx, gw_prob, sw))
        elif not same_winner:
            coinflip_flips += 1
        worst.append((wd, idx, f"win hw={sw} py={gw} (Δ{wd}); spread hw={ss} py={gs} (Δ{sd})"))

    scored = len(win_deltas)
    if scored == 0:
        print("\nNo scorable games (all timeout/missing) — FAIL"); sys.exit(1)

    win_mean = sum(win_deltas) / scored
    win_max  = max(win_deltas)
    sp_mae   = sum(spread_deltas) / scored
    sp_max   = max(spread_deltas)

    print(f"\n{'='*56}")
    print(f"Functional regression: {n} games  ({scored} scored, {timeouts} timeouts)")
    print(f"  Winner agreement   : {agree}/{scored} ({agree/scored:.1%})")
    print(f"  Confident-game agree: {confident - len(confident_flips)}/{confident} "
          f"(games |prob-0.5|>={COINFLIP_BAND}; {coinflip_flips} excused coin-flip(s))")
    print(f"  Win delta (cnts)   : mean {win_mean:.1f}  max {win_max}  (C-sim envelope ~12 / ~25)")
    print(f"  Spread delta(pts)  : MAE  {sp_mae:.2f}  max {sp_max}")
    print(f"{'='*56}")
    if confident_flips:
        print("CONFIDENT-GAME WINNER FLIPS (real bugs):")
        for idx, p, sw in confident_flips:
            print(f"  game {idx}: model prob {p:.3f} but hw byte {sw} ({sw/256:.3f})")
    print("Worst 5 by win delta:")
    for wd, idx, d in sorted(worst, reverse=True)[:5]:
        print(f"  game {idx}: {d}")

    ok = (timeouts == 0 and len(confident_flips) == 0
          and win_max <= WIN_MAX_TOL and win_mean <= WIN_MEAN_TOL and sp_max <= SPREAD_MAX_TOL)
    print(f"\nPass criteria: timeouts=0, NO confident-game winner flips, "
          f"win_max<={WIN_MAX_TOL}, win_mean<={WIN_MEAN_TOL}, spread_max<={SPREAD_MAX_TOL}")
    print("OVERALL:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
