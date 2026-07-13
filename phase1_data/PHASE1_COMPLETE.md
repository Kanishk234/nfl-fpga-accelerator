# Phase 1 — Data Pipeline: Complete

## What Was Built

Five Python modules that pull raw NFL data, engineer features, and produce
the two artifacts every downstream phase depends on.

| File | Role |
|---|---|
| `elo.py` | Computes pre-game Elo ratings for every team, every game, 2000–2024 |
| `features.py` | Computes rolling 4-game averages, win streaks, rest days, game context, and vegas_total |
| `validate.py` | Asserts all 21 features are present, non-null, and in valid ranges; saves artifacts |
| `pipeline.py` | Entry point — runs all steps in order |
| `qb.py` | QB rolling EPA/CPOE pipeline (built, tested, not wired in — see improvement log) |

## What It Produced

```
data/processed/games.parquet   — 6,427 completed regular-season games (2000–2024)
artifacts/features.json        — locked list of 21 features in FPGA-mandated order
```

## How to Re-Run It

```bash
source venv/bin/activate
python phase1_data/pipeline.py
```

Takes about 30–60 seconds (nflreadpy fetches data from the internet on first run;
subsequent runs use the cached `data/raw/schedules_raw.parquet`).

## How Tests Were Run & What They Verified

```bash
source venv/bin/activate
pytest tests/test_phase1.py -v
```

**Result: 14/14 passed.**

| Test | What it checks |
|---|---|
| `test_all_teams_start_at_base_elo` | Every team begins at Elo 1500 before their first game |
| `test_pre_game_elo_equals_post_game4_elo` | Game 5's stored Elo = what was computed after game 4 (no leakage) |
| `test_elo_within_bounds_on_full_dataset` | All Elos stay between 1000 and 2200 across 25 seasons |
| `test_game_n_rolling_excludes_game_n_score` | Rolling avg for game N does not include game N's own score |
| `test_window_of_4_uses_correct_prior_games` | Window=4 uses exactly the right prior games (verified with known scores) |
| `test_exactly_21_feature_columns` | Output has exactly 21 feature columns — not 20, not 22 |
| `test_zero_nulls_in_features_and_labels` | Zero nulls in any feature or label after pipeline |
| `test_home_win_rate_in_expected_range` | Home win rate is 0.54–0.60 (actual: 0.560) |
| `test_spread_mean_positive` | Home teams outscore away teams on average (actual mean: +2.22) |
| `test_spread_std_in_expected_range` | Spread std is 10–20 points (actual: 14.65) |
| `test_games_sorted_by_season_week` | Dataset is in chronological order — no game appears before an earlier one |
| `test_features_json_exists` | `artifacts/features.json` exists on disk |
| `test_features_json_has_correct_structure` | JSON has a `features` key with exactly 21 entries |
| `test_features_json_order_matches_canonical` | JSON feature order exactly matches `CANONICAL_FEATURES` |

## Key Design Decisions & Why

- **Elo K=20, HOME_ADVANTAGE=48** — FiveThirtyEight's calibrated NFL values
- **shift(1) on all rolling stats** — game N can only use games 1..N-1; no leakage
- **Temporal sort before Elo** — Elo must be computed in game order or ratings are wrong
- **MinMaxScaler (Phase 2)** — maps to INT8 cleanly for FPGA quantization; not StandardScaler
- **21 features, order locked** — the FPGA input bus width is set at synthesis time
- **temp/wind** — high wind and cold suppress scoring; dome games filled with 65°F / 0 mph
- **is_div_game** — divisional games are consistently tighter regardless of Elo gap
- **vegas_total** — Vegas over/under encodes expected scoring environment; orthogonal to spread_line. Tested with 5-seed harness: +0.011 win accuracy (~3× noise band). Appended at index 20 so existing byte offsets are unchanged.

## Feature Improvement Campaign (What Was Tried)

Features are only locked after Phase 4 (hls4ml synthesis). All candidates were
validated with `phase2_model/eval_repeated.py` (5 seeds, mean±std) before keeping
or reverting. Single-run deltas below ~0.004 are noise.

| Candidate | Win acc Δ | AUC Δ | MAE Δ | Verdict |
|---|---|---|---|---|
| MOV-adjusted Elo | ~0 | ~0 | ~0 | Reverted — redundant with vegas_spread |
| `vegas_total` (+1 feat) | **+0.011** | −0.0005 | +0.007 | **Kept** — 3× noise band gain |
| Team EPA from `load_team_stats` (+4) | −0.007 | −0.001 | +0.008 | Reverted — redundant with pts_avg + Vegas |
| Recency sample-weighting | −0.027 | −0.008 | +0.161 | Reverted — NFL patterns stable across eras |
| QB rolling EPA+CPOE (+4 feat) | −0.007 | **−0.006** | +0.009 | Reverted — pre-2016 CPOE gap creates noise |

The QB pipeline (`qb.py`) was fully built and achieves 92.7% game coverage by
joining on `(season, week, team, player_id)` rather than `game_id` (which is NaN
pre-2016 in nflreadpy). It's kept as dead code in case the approach is revisited
with post-2016-only training or a better cold-start prior.

## Sanity Check Numbers (from last run)

```
Games loaded:     6,427  (2000–2024 regular season, completed games only)
Games dropped:       20  (insufficient rolling history — first 1-2 games of season)
Home win rate:    0.560  (expected ~0.57 — within normal range)
Spread mean:      +2.22  (home teams score more on average)
Spread std:       14.65  (typical NFL game margin variability)
Top 2024 Elos:    KC 1641, BUF 1623, DET 1620, PHI 1614, BAL 1599
Features:            21  (added temp, wind, is_div_game in v2; vegas_total in v3)
```
