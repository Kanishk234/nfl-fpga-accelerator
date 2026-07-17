# Model Improvement Ideas

## Results log (validated with seeded multi-run harness)
Measurement note: single-run win accuracy has ~±0.0036 std (baseline) from random
init/shuffling. Always judge changes with `phase2_model/eval_repeated.py` (5 seeds,
mean±std) — a delta smaller than the std is noise. `train.py` now uses a fixed seed (42)
so the committed model is reproducible.

| Idea | Win acc Δ | AUC Δ | Spread MAE Δ | Verdict |
|---|---|---|---|---|
| MOV-adjusted Elo | ~0 | ~0 | ~0 | Neutral → **reverted** (redundant with vegas_spread) |
| `vegas_total` feature | **+0.0114** (0.642→0.654) | −0.0005 | +0.007 | **KEPT** — real win-acc gain (~3× noise band), neutral elsewhere |
| EPA from `load_team_stats` (+4 feat) | −0.007 | −0.001 | +0.008 | Neutral → **reverted** — EPA correlates too heavily with existing pts_avg + Vegas line |
| Recency sample-weighting (min_w=0.25) | −0.027 | −0.008 | +0.161 | **Clearly harmful** → reverted. Discards ~40% of training data; NFL patterns are stable across eras at this feature level |
| QB rolling EPA+CPOE (+4 feat, fixed join) | −0.007 | **−0.006** | +0.009 | **Reverted** — AUC drop 2× noise band. Pre-2016 cold-start zeros (~40% of data) inject noise that drowns post-2016 signal. CPOE unavailable pre-2016. |

Baseline (21 feat, 5 seeds): win_acc 0.6538±0.0067, AUC 0.7074±0.0029, MAE 9.772±0.030.
Previous baseline (20 feat, 5 seeds): win_acc 0.6424±0.0036, AUC 0.7079±0.0033, MAE 9.765±0.035.

---


Goal: push **win accuracy** and **AUC** higher and **spread MAE** lower, while staying
FPGA-safe (fits Basys 3 synthesis constraints) and consistent with the bigger project plan.

## Current baseline (Phase 2, validation 2021–2022)
- Win accuracy: **64.6%** (target ≥63%, always-home baseline 53.6%)
- Win AUC: **0.707**
- Spread MAE: **9.74 pts** (Vegas opening line baseline 9.76 — we already beat it)
- Spread RMSE: 12.62 pts
- Params: 13,090 / 50,000 budget

## Strategic timing note
Feature count/order is only locked **after Phase 4 (hls4ml synthesis)**. We are between
Phase 2 and Phase 3, so **feature changes are free right now and expensive forever after.**
The highest-payoff levers in NFL modeling are feature-side → do the big feature work *now*,
before quantizing and locking.

> Note: `CLAUDE.md` still says "feature count fixed at 17" — stale, we're at 20. Fix it.

---

## Tier 1 — Highest leverage, FPGA-safe

### 1. EPA-based rolling team stats *(biggest known lever)*
Replace raw points-scored/allowed with Expected Points Added per play (offense EPA/play,
defense EPA/play, success rate). Far more predictive than raw points. Needs `load_pbp`
(heavy one-time pull) + leakage-free `.shift(1)` rolling like the current stats. Swap out
weak features (temp, wind) to keep the vector lean.
- Impact: high on both accuracy and spread. Effort: medium-high.

### 2. QB rolling features
Raw data has `home_qb_id`/`away_qb_id`/names. Rolling per-QB EPA/dropback or passer rating,
shifted, with cold-start handling (rookies / mid-season swaps → league-average prior).
Currently zero QB signal; QB is the highest-variance position.
- Impact: high. Effort: high (mid-season changes, injuries, rookies).

### 3. Margin-of-victory-adjusted Elo (538-style)  ← STARTING HERE
Current Elo update is vanilla `K*(actual-expected)`. Add a MOV multiplier so blowouts move
Elo more than narrow wins. Changes only the *values* of existing features
(`home_elo`/`away_elo`/`elo_diff`) — **no feature count/order change, no synthesis impact.**
Also tune K / home-advantage / season-reversion against val.
- Impact: moderate. Effort: low. No interface change.

---

## Tier 2 — Easy, FPGA-safe, signals already on disk
(These columns are already pulled in Phase 1 but unused.)

### 4. Add `total_line` (Vegas over/under) as a feature
Encodes expected pace/scoring environment → directly informs spread magnitude. One feature.

### 5. Use `home_rest`/`away_rest` + a `rest_diff` feature
Schedule has clean rest values; we currently recompute our own. Add rest differential and
short-week/bye flags.

### 6. Exponential-decay recent form instead of flat 4-game mean
Flat window weights game N-4 same as N-1. Exponential weighting tracks hot/cold teams better.
Same feature count, better values.

### 7. Fix `vegas_spread` missing-value handling
`fillna(0.0)` = pick'em, which is wrong for missing lines (injects fake neutral games).
Fill from Elo-implied spread, or drop those rows.

### 8. Opponent-adjusted (strength-of-schedule) rolling stats
Adjust rolling stats by opponent Elo — 28 pts vs a bad defense ≠ vs a good one.

---

## Tier 3 — Architecture & training (within constraints)

### 9. BatchNorm between dense layers
hls4ml-supported, folds into an affine transform at inference → FPGA-safe. Often improves
convergence/generalization on tabular nets. Keeps ReLU-only hidden activations.

### 10. Recency sample-weighting
Weight 2018–2020 seasons more than 2000–2005 (passing-era league shift). Training-only.

### 11. Structured hyperparameter sweep w/ expanding-window temporal CV
Replace one-at-a-time manual tweaks. Sweep width/depth/lr/dropout/loss-weight, select on val.
Lots of param headroom (13k of 50k).

### 12. Spread head reframing
Predict residual vs `vegas_spread` instead of raw spread — often easier than absolute margin.
Training-side only.

---

## Avoid (not FPGA-safe)
- Ensembles / XGBoost on-chip (single model per bitstream). OK only as offline ceiling benchmark.
- Team/QB embedding layers (large lookup tables, awkward on Basys 3). Rolling stats sidestep this.
- Non-ReLU hidden activations, StandardScaler (hard-constrained).
- Very wide layers that blow the DSP/BRAM budget despite param headroom.

---

## Recommended order
1. **MOV-Elo** — ~~cheap, no interface change~~ → tested, neutral, reverted.
2. **`total_line`** feature — ~~one-liner~~ → tested, +0.011 win acc, **kept**.
3. **EPA rolling (load_team_stats)** → tested, neutral, reverted.
4. **Recency weighting** → tested, harmful, reverted.
5. **QB rolling EPA+CPOE** → tested, AUC hurt 2× noise band, reverted. Pre-2016 coverage gap is the blocker.
6. **Next candidate**: EPA-based rolling from play-by-play (`load_pbp`) — heavier pull but avoids pre-aggregation issues. Or hyperparameter sweep (Tier 3, item 11).

## Reality check
Spread MAE is near its irreducible floor (already beating Vegas at 9.74 vs 9.76). Expect
**accuracy/AUC to have more room than spread MAE.** Better features lift accuracy more
reliably than they crack the spread wall.
