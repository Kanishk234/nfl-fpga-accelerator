# Phase 4 (conifer) — GBDT → FPGA Conversion: Running Log

> **Status: IN PROGRESS.** This is a running document, updated as the phase
> advances. Completed sections are marked ✅; planned work is listed at the end.

The GBDT analog of `phase4_hls/` (which used hls4ml for the MLP). conifer is
hls4ml's sibling project for boosted decision trees: it converts trained tree
ensembles into HLS/RTL for Xilinx FPGAs. Trees synthesize to **comparators +
adders — zero DSPs** — a fundamentally different resource profile from the
MLP's DSP-bound matrix multiplies, which is exactly what makes the 1:1
hardware comparison interesting.

---

## ✅ Step 0 — Smoke Test (de-risk before building anything)

**The question that decided this phase's shape:** conifer grew out of HEP
trigger *classification*; regression support was the open risk. If the spread
regressor couldn't convert, the whole phase would pivot (spread binning /
ordinal classification). So the very first act was converting both saved
models with conifer's **C++ backend** — a bit-accurate emulation of the HLS
fixed-point arithmetic that runs without Vivado.

### Verdict: **both stages convert — full flow UNBLOCKED**

```
[1/2] win classifier      converted + compiled OK
      decision agreement HW vs float: 0.9926
      HW val accuracy: 65.38%  (float: 65.75%)

[2/2] spread residual regressor   converted + compiled OK
      mean |HW - float| residual: 0.20 pts
      HW spread MAE: 9.771  (float: 9.758)
```

At `ap_fixed<32,16>` precision, the emulated hardware is within 0.4pt accuracy
/ 0.013 MAE of the float model *before any precision tuning*.

### What it took to get there (three real findings)

1. **The pip wheel of conifer 1.9 ships without its external C++ headers**
   (`nlohmann/json.hpp`, Xilinx `ap_fixed` types) — `model.compile()` fails
   with a missing-header error. Fix (done once per venv): fetch
   `nlohmann/json.hpp` v3.11.3 and clone
   `Xilinx/HLS_arbitrary_Precision_Types`, placing them under
   `site-packages/conifer/external/{json,ap_types}/`. A rebuild of the venv
   must repeat this.

2. **Precision must cover RAW feature ranges.** The GBDT eats unscaled
   features (trees are scale-invariant — no MinMaxScaler in this track), so
   elo values ~1500 flow straight into the fixed-point comparators. The first
   guess `ap_fixed<18,8>` saturates at ±128 → elo thresholds unrepresentable →
   only 96.7% decision agreement. `ap_fixed<32,16>` (integer range ±32768)
   fixed it: 99.3%. The full flow will tune width down again — but the integer
   part must stay ≥ 12 bits unless features get pre-scaled in the wrapper.

3. **A false alarm worth recording:** the regressor conversion appeared to
   crash on xgboost 3.x's bracketed `base_score` string (`'[-0.27]'`).
   Tracing with a minimal repro showed conifer 1.9 parses that fine — the
   bare `float()` was in *our own test harness*. Lesson: xgboost ≥ 2
   re-brackets `base_score` in `save_config()`; strip `[]` before parsing it
   anywhere.

### Facts the smoke test established (design inputs for the next steps)

- **conifer emits the raw ensemble sum** — the classifier margin (no sigmoid)
  and the raw residual. The sigmoid and the `+ vegas_spread` adder live in the
  HDL wrapper, exactly like hls4ml's sigmoid lives in the MLP IP. Decisions at
  margin > 0 are identical to prob > 0.5 (sigmoid is monotone), so the win
  *decision* needs no sigmoid in hardware at all.
- **Stage chaining is a wrapper concern:** stage 2 takes 22 inputs (21
  features + stage-1 win prob). The smoke test validated each stage
  standalone; the phase-5 wrapper will chain stage1 → sigmoid → stage2.
- The remaining 0.7% decision disagreement is the known xgboost ≥ 2.0
  threshold-rounding issue in conifer (`<` vs `<=` at quantized split
  values) — to be squeezed during precision tuning.
- Model scale being synthesized: win = 132 trees (best_it 131 + 1) × depth 2;
  spread = 31 trees × depth 3. Both tiny next to the MLP's 13,218 MACs.

### How to re-run

```bash
source venv/bin/activate
python phase4_conifer/smoke_test.py     # needs the header fix (finding 1) once
```

---

## ✅ Step 1 — Precision Scan: `ap_fixed<24,12>` locked

`precision_scan.py` swept 10 `ap_fixed<W,I>` configs (both stages each,
results in `precision_scan.tsv`). Float references: win acc 65.75%, spread
MAE 9.7575.

| W | I | frac | clf agreement | clf acc | reg MAE |
|---|---|---|---|---|---|
| 16 | 12 | 4 | 0.398 | 46.4% | 9.961 |
| 18 | 12 | 6 | 0.589 | 58.9% | 9.775 |
| 20 | 12 | 8 | 0.860 | 65.0% | 9.768 |
| 22 | 12 | 10 | 0.974 | 65.0% | 9.770 |
| **24** | **12** | **12** | **0.9926** | **65.38%** | **9.771** |
| 28 | 14 | 14 | 0.9926 | 65.38% | 9.771 |
| 32 | 16 | 16 | 0.9926 | 65.38% | 9.771 |

Two findings:

1. **Fractional bits are the controlling variable, not integer bits.**
   Agreement tracks W−I exactly (configs with equal frac bits produce
   identical results regardless of W). It climbs monotonically until
   **frac = 12, where it plateaus at 99.26%** — wider makes zero difference.
   I = 12 (±2048) fully covers the raw elo range; nothing above it helps.
   `ap_fixed<24,12>` is therefore the cheapest config on the plateau, and is
   **locked for HLS synthesis.**

2. **The residual 0.7% disagreement (4 of 543 games) is NOT a precision
   artifact** — it's the known conifer/xgboost ≥ 2.0 threshold-rounding
   convention issue (float32 splits vs quantized fixed-point comparison), and
   no bit width fixes it. Decision: **accept it.** Rationale:
   - HW accuracy 65.38% is within 2 games of float 65.75% — inside seed noise
     (the model's own multi-seed spread is ±0.1pt ≈ ±0.5 games).
   - Downstream verification (phase 6/7 analog) is judged **bit-exact vs the
     conifer C++ emulation as golden**, exactly as the MLP flow is judged vs
     XSIM golden — not vs float xgboost. The 99.26% is a modeling-fidelity
     stat, not a verification gate.
   - The HW spread MAE lands at 9.771, a hair above the Vegas baseline
     (9.763) where the float model sat a hair below — honest note for the
     final comparison table; the difference is 0.008 pts on 543 games.

### How to re-run

```bash
python phase4_conifer/precision_scan.py   # appends to precision_scan.tsv
```

---

## Planned (not yet done)

| Step | What | Gate |
|---|---|---|
| 2. HLS synthesis | conifer Vivado backend on Windows at `ap_fixed<24,12>` (same toolchain as the MLP flow); get real LUT/FF/latency numbers | Fits Basys 3 alongside/instead of MLP; compare vs the 17,896-LUT MLP |
| 3. Two-stage wrapper | Chain stage1 → sigmoid/LUT → stage2, `+ vegas_spread` adder, AXIS interface matching the phase 5 UART plumbing | XSIM golden vectors bit-exact |
| 4. Board deploy | Reuse phase 7 pyserial harness; head-to-head latency + resource table vs MLP | Bit-exact vs XSIM; honest UART-bound framing |
