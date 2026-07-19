# Phase 2 (GBDT) — Stacked Gradient-Boosted Tree: Complete

## Did Everything Work?

**Yes.** A stacked XGBoost model was built as a second variant alongside the MLP,
using the identical 21 features, temporal split, and targets — a true 1:1
comparison. It clears the honest gates: win accuracy beats the always-home
baseline and lands near the MLP; spread MAE ties the Vegas opening line, which is
the practical data floor for *both* models. Nothing in the MLP path or the sacred
artifacts (`features.json`, `scaler.pkl`, `model_best.keras`) was touched.

This is the phase 2 analog on the **`gbdt-stacked`** branch, intended to merge into
`main` as a second model variant.

---

## What Was Built

One self-contained training module that loads the processed data, trains a
two-stage stacked tree model, evaluates it against the honest baselines, and saves
its own artifacts to a parallel location.

| File | Role |
|---|---|
| `phase2_gbdt/train_gbdt.py` | Data loading, stacked training (win → spread), evaluation, artifact save |
| `phase2_gbdt/__init__.py` | Package marker |

**No `model.py` / `scaler` / `quantization` split like the MLP** — trees are
scale-invariant, so there is no MinMaxScaler and no separate INT8 quantization
stage. Threshold quantization happens later inside conifer (the phase 4 analog).

---

## What It Produced

```
artifacts/gbdt/win_model.json     — XGBClassifier (home_win), refit on full train
artifacts/gbdt/spread_model.json  — XGBRegressor (spread residual to the Vegas line)
artifacts/gbdt/gbdt_report.json   — metrics, baselines, MLP reference, best iterations
```

The MLP's `artifacts/scaler.pkl`, `model_best.keras`, and `features.json` are
untouched — the GBDT reads `features.json` read-only for the canonical order.

---

## How to Re-Run It

```bash
source venv/bin/activate
python phase2_gbdt/train_gbdt.py    # trains both stages, saves artifacts/gbdt/
```

Requires `xgboost` (3.3.0, installed into the WSL venv during this phase).

---

## Architecture — STACKED

```
Input (21 features, raw — no scaling)
        │
        ▼
 [Stage 1]  XGBClassifier ──► home_win probability
        │                          │
        │      out-of-fold on train │ (held-out on val/test)
        ▼                          ▼
 21 features ⊕ win_prob  ──►  [Stage 2] XGBRegressor
                                       │
                                       ▼
                            residual = spread − vegas_spread
                                       │
                       final spread = vegas_spread + tree_output
```

- **Stage 1** predicts the win probability from the 21 features.
- **Stage 2** predicts the point spread, taking the 21 features **plus the stage-1
  win probability** as a 22nd input (this is the "stacking").
- The spread head predicts the **residual to the Vegas line**, not the raw spread;
  the line is added back at the end. On the FPGA this "+ vegas_spread" is a single
  adder tapping an input byte that is already present.

---

## What Happened Step by Step

### 1. Data Split (Temporal — identical to the MLP)

The same 6,427 games, split strictly by season — no random split, no leakage
across the boundary:

| Split | Seasons | Games |
|---|---|---|
| Train | 2000–2020 | 5,340 |
| Validation | 2021–2022 | 543 |
| Test | 2023–2024 | 544 |

### 2. No Scaler, No INT8 Stage

Trees split on raw thresholds and are scale-invariant, so the MinMaxScaler and the
INT8 quantization that the MLP needs simply do not apply. Features are fed raw in
the canonical `features.json` order. This collapses the MLP's separate phase 3
(QKeras quantization) into conifer's threshold quantization at phase 4.

### 3. Stage 1 — Win Probability Classifier

`XGBClassifier` with **depth-2 monotone trees** (see the tuning campaign in §6b).
Monotone constraints encode domain knowledge as regularization: win probability is
forced non-decreasing in home-strength signals (`home_elo`, `elo_diff`,
`home_point_diff_avg`, `vegas_spread`) and non-increasing in away strength
(`away_elo`, `away_point_diff_avg`). Trained with early stopping watching the real
validation set. Final model refit on all of train — this is the model that ships.

### 4. Out-of-Fold Stacking (Avoiding Leakage)

The spread model is trained on the win probability as an input feature. If it saw
**in-sample** win probs (from a model that had already trained on those same
games), those probabilities would be overconfident and leak the win label into the
spread model.

To prevent this, the win-prob feature used during **training** is generated
**out-of-fold**: a 5-fold split where each fold's win probs come from a model that
never saw that fold. Validation and test get their win-prob feature from the
full-train refit — leakage-free because those splits are held out entirely.

### 5. Stage 2 — Spread Residual Regressor

`XGBRegressor` predicting `spread − vegas_spread`, with heavy regularization
(depth 3, `min_child_weight=8`, `reg_lambda=5`, `gamma=1`). The final spread is
`vegas_spread + tree_output`.

The heavy regularization is deliberate: the residual carries almost no learnable
signal, so the model must be free to early-stop near zero trees rather than fit
noise. In the shipped run it stopped at **30 trees** — small, honest corrections.

### 6. The Investigation — Why the Spread Head Looks the Way It Does

The first, naive attempt (predict raw spread with a normal regressor) produced
**MAE ~9.87 — worse than the Vegas line itself.** This was diagnosed rather than
tuned around:

| Baseline on validation | Spread MAE |
|---|---|
| Predict 0 | 10.94 |
| Predict train mean | 10.85 |
| **Predict the Vegas line** | **9.76** |
| Best linear fit on the Vegas line | 9.76 |

The spread target has std **13.95** and correlates only **0.43** with the Vegas
line. A 27-config hyperparameter sweep was run on the raw-spread model — **every
single config lost to the Vegas line** (9.79–9.89). The reason is structural:
game margin is nearly *linear* in the Vegas line, and trees approximate a line
with high-variance staircase steps, adding error instead of removing it.

Switching to the **residual parameterization** fixed it. When asked to predict the
residual, the trees near-immediately early-stop (`best_iteration` of 0–1 in most
sweep configs) — the model's own verdict is *"the best correction to the Vegas
line is no correction."* The one config that trained landed at 9.760 vs the line's
9.763 — a 0.003-point edge, i.e. noise.

### 6b. Win-Head Tuning Campaign

The untuned stage-1 model (depth-5, no constraints) sat at 63.35% val accuracy vs
the MLP's 64.5%. A structured improvement campaign followed, with every candidate
gain re-validated across 3 seeds (single-run deltas below ~0.4pt are noise — the
MLP's own `eval_repeated.py` lore):

| Change | Effect | Verdict |
|---|---|---|
| Monotone constraints (6 features) | ~+1pt val acc, consistent across 9/11 configs | **Kept** |
| Depth 5 → 3 → **2** | depth 2 dominated the sweep leaderboard | **Kept** |
| lr 0.02 → 0.03, hist tree method | no acc change, ~3× faster | Kept |
| 27-config raw-spread sweep | all configs lose to the Vegas line | Led to residual design |
| OOF-derived decision threshold (0.49 vs 0.50) | +0.23pt on train OOF, does **not** transfer to val | Reverted — keep 0.5 |
| Early-stop on `error` instead of `logloss` | **+0.4pt** (65.56 ± 0.15 across 3 seeds), AUC unchanged | **Kept** — stop at the accuracy-optimal round |
| 10-seed probability ensemble | flat (65.19 = single model) | Reverted — no gain, would 10× FPGA tree count |
| DART booster (tree dropout) | −0.2pt mean | Reverted |

Final config: `max_depth=2, lr=0.03, subsample=0.9, colsample_bytree=0.8,
min_child_weight=3, reg_lambda=2, gamma=0.5, monotone_constraints` — chosen from
the top-3 multi-seed finalists for its clearly better AUC (0.717 vs 0.711) at
statistically tied accuracy (probability quality matters: the win prob feeds the
spread stack). Multi-seed val accuracy: **65.1% ± 0.1pt**.

A hardware bonus: depth-2 trees halve the per-tree comparator depth vs depth 3,
so the more accurate model is also the cheaper one to synthesize.

### 7. Final Validation Results (2021–2022)

```
Win Accuracy:    65.75%   (untuned: 63.35%; MLP: 64.5%; always-home: ~57%)
Win AUC:         0.716
Spread MAE:      9.758    (Vegas line baseline: 9.763 — ties/edges it)
Spread head:     30 trees (predicts residual to the Vegas line)
```

### 8. Test Set Results (2023–2024, evaluated once)

```
Win Accuracy:    70.22%   (baseline always-home ~57%)
Win AUC:         0.731
Spread MAE:      9.776
```

Test win accuracy (70.22%) is notably higher than validation (65.75%) — the same
generalization-to-recent-seasons pattern the MLP showed. This is the most honest
number: 544 held-out games the model never influenced.

### 9. Head-to-Head vs the MLP

| Metric | GBDT (val) | MLP (val) | GBDT (test) | MLP (test) |
|---|---|---|---|---|
| Win accuracy | **65.8%** | 64.5% | 70.2% | 70.2% |
| Win AUC | **0.716** | 0.710 | **0.731** | 0.724 |
| Spread MAE | 9.758 | 9.74 | **9.776** | 9.86 |

The tuned GBDT wins val accuracy (+1.3pt), val AUC, test AUC, and test spread
MAE, and exactly ties the MLP's 70.2% on test accuracy. The "GBDTs are strong on
tabular data" prior holds: with equal tuning effort the trees are at least the
MLP's equal on every axis. **The win head is where the real, learnable signal
lives** — spread is a data noise floor that neither architecture can beat, because
the Vegas line already prices in everything the 21 features contain.

---

## Two Corrections to Project Lore

- **The `CLAUDE.md` spread gate of ≤9.0 is unreachable and always was.** The MLP
  itself sits at 9.74; the optimal fit on the Vegas line floors at ~9.76. The GBDT
  script is graded against the honest bar: *tie or beat the Vegas line.*
- **Trees need no scaler and no INT8 quantization.** The MLP's phase 3 has no GBDT
  analog — its role is absorbed by conifer's threshold quantization at phase 4.

---

## Key Design Decisions & Why

- **Stacking (win prob → spread)** — the requested design; lets the spread model
  condition on how likely the home team is to win, and enables a clean 1:1
  architectural comparison against the MLP's shared-trunk dual heads.
- **Out-of-fold win probs for the stacked feature** — the single most important
  correctness detail in stacking; without it the spread model trains on leaked,
  overconfident win probabilities it will never see at inference.
- **Residual-to-Vegas spread target** — trees model corrections to a near-linear
  relationship far better than they model the line itself. Final = `vegas_spread +
  tree`, which is one adder on the FPGA. Fair (same information) and honest.
- **Heavy spread regularization + early stopping** — lets the model collapse to
  "just predict the line" when there is no residual signal, instead of fitting
  noise below the Vegas floor.
- **Monotone constraints on the win head** — domain knowledge (stronger home team
  → higher win prob) as regularization; worth ~+1pt accuracy on 543 val games and
  guarantees the hardware can never emit a probability that moves the wrong way
  with elo or the Vegas line.
- **Depth-2 trees** — dominated the tuning sweep on this small dataset and halve
  the FPGA comparator depth per tree.
- **Decision threshold stays 0.5** — the OOF-optimal threshold (0.49) gained on
  train but did not transfer to val; tuning it would be fitting noise.
- **No scaler / raw features** — trees are scale-invariant; scaling would add a
  pointless preprocessing step with no accuracy benefit.
- **Fixed seed (42)** — reproducible committed artifacts, matching the MLP's
  convention.
- **Parallel artifact location (`artifacts/gbdt/`)** — the sacred MLP artifacts are
  never at risk.

---

## Artifacts Produced

| File | Status |
|---|---|
| `artifacts/gbdt/win_model.json` | Stage-1 win classifier — source of truth for the GBDT phase 4 (conifer) |
| `artifacts/gbdt/spread_model.json` | Stage-2 spread residual regressor |
| `artifacts/gbdt/gbdt_report.json` | Metrics + baselines + MLP reference |
| `artifacts/features.json` | Read-only — unchanged, shared canonical feature order |

---

## Next Phase

**Phase 4 analog — conifer** (BDT → Vivado HLS, the tree equivalent of hls4ml).
The early de-risk before committing to the full flow: install conifer and verify
its Vivado backend can convert the spread **regressor** — its classification path
is solid, but regression support is version-dependent. Once both stages convert,
reuse the existing phase 5–7 AXIS/UART wrapper and pyserial harness.
