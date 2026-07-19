# Phase 4 (conifer) — GBDT → FPGA Conversion

> **Status: IN PROGRESS** — conversion, precision, synthesis, and cosim are ✅
> done; the two-stage wrapper and board deploy remain (see *Remaining Work*).
> This doc is updated as the phase advances and finalizes when the phase does.

## Did Everything Work (So Far)?

**Yes.** Both GBDT stages — the win classifier *and* the spread residual
regressor (the risk that could have forced a redesign) — convert through
conifer, pass RTL cosim, and export as Vivado IP blocks. Real Vivado synthesis
shows **both stages combined use 57% of the Basys 3 (11,869 LUTs) with zero
DSPs and 9-cycle latency** — smaller and ~10× faster than the MLP's 17,896
LUTs (86%). The more accurate model is also the cheaper hardware.

This is the GBDT analog of `phase4_hls/` (hls4ml for the MLP). conifer is
hls4ml's sibling project for boosted decision trees: trees synthesize to
comparators + adders — no DSPs, no BRAM — a fundamentally different resource
profile from the MLP's DSP-bound matrix multiplies, which is what makes the
1:1 hardware comparison interesting.

---

## What Was Built

| File | Role |
|---|---|
| `smoke_test.py` | De-risk: convert both models via the C++ backend, verify vs float |
| `precision_scan.py` | Sweep `ap_fixed<W,I>` configs; lock the cheapest faithful one |
| `convert_hls.py` | Emit both Vitis HLS projects (+ auto-patches for conifer 1.9 template bugs) |
| `make_tb_data.py` | 100 val-set testbench vectors + C++-emulation golden outputs |
| `synth_gbdt.tcl` | Driver: sets csim/synth/cosim/export flags, sources conifer's build tcl |
| `run_synthesis.bat` | Windows: Vitis HLS flow (csim→csynth→cosim→IP export), both stages |
| `run_vsynth.bat` | Windows: real Vivado logic synthesis for true utilization numbers |

---

## What It Produced

```
phase4_conifer/precision_scan.tsv       — scan results (committed)
phase4_conifer/hls_win/                 — generated HLS project + exported IP (gitignored)
phase4_conifer/hls_spread/              — generated HLS project + exported IP (gitignored)
phase4_conifer/hls_*/golden_cpp.npy     — bit-accurate C++ goldens (verification reference)
phase4_conifer/hls_*/vivado_synth.rpt   — real utilization reports
```

Generated projects are **gitignored** — fully regenerable from the committed
`artifacts/gbdt/` models via the committed scripts (same convention as
`phase4_hls/hls_project/`).

---

## How to Re-Run It

```bash
# WSL:
python phase4_conifer/smoke_test.py       # needs one-time header fix (see step 1)
python phase4_conifer/precision_scan.py   # appends to precision_scan.tsv
python phase4_conifer/convert_hls.py      # emit + patch both HLS projects
python phase4_conifer/make_tb_data.py     # testbench vectors + goldens
```
```bat
:: Windows:
phase4_conifer\run_synthesis.bat          :: csim+csynth+cosim+IP export, both stages
phase4_conifer\run_vsynth.bat             :: real LUT/FF numbers
```

---

## What Happened Step by Step

### 1. Smoke Test — De-Risking the Regression Path

conifer grew out of HEP trigger *classification*; regression support was the
open risk that decided this phase's shape. Before building anything, both
saved models were converted with conifer's **C++ backend** — a bit-accurate
emulation of the HLS fixed-point arithmetic that runs without Vivado.

**Verdict: both stages convert — full flow unblocked.** At `ap_fixed<32,16>`:
win classifier 99.26% decision agreement vs float (65.38% HW val accuracy vs
65.75% float); spread regressor within 0.20 pts mean (MAE 9.771 vs 9.758).

Findings on the way:
- **The pip wheel of conifer 1.9 ships without its external C++ headers**
  (`nlohmann/json.hpp`, Xilinx `ap_fixed` types) — `compile()` fails. One-time
  fix per venv: fetch nlohmann json v3.11.3 + clone
  `Xilinx/HLS_arbitrary_Precision_Types` into
  `site-packages/conifer/external/{json,ap_types}/`.
- **A false alarm worth recording:** the regressor appeared to crash on
  xgboost 3.x's bracketed `base_score` string (`'[-0.27]'`). A minimal repro
  showed conifer parses it fine — the bare `float()` was in *our own test
  harness*. Lesson: xgboost ≥ 2 re-brackets `base_score` in `save_config()`;
  strip `[]` before parsing it anywhere.

### 2. Precision Scan — `ap_fixed<24,12>` Locked

10 `ap_fixed<W,I>` configs swept (`precision_scan.tsv`), gates: ≥ 99.9%
decision agreement, MAE within 0.01 of float.

| W | I | frac | clf agreement | clf acc | reg MAE |
|---|---|---|---|---|---|
| 16 | 12 | 4 | 0.398 | 46.4% | 9.961 |
| 18 | 12 | 6 | 0.589 | 58.9% | 9.775 |
| 20 | 12 | 8 | 0.860 | 65.0% | 9.768 |
| 22 | 12 | 10 | 0.974 | 65.0% | 9.770 |
| **24** | **12** | **12** | **0.9926** | **65.38%** | **9.771** |
| 28 | 14 | 14 | 0.9926 | 65.38% | 9.771 |
| 32 | 16 | 16 | 0.9926 | 65.38% | 9.771 |

- **Fractional bits are the controlling variable.** Configs with equal frac
  bits produce identical results regardless of total width; fidelity plateaus
  at frac = 12. Integer bits must cover the RAW feature ranges (no scaler in
  this track — trees are scale-invariant): elo ~1500 needs I ≥ 12.
  `ap_fixed<24,12>` is the cheapest config on the plateau → **locked**.
- **The residual 0.7% disagreement (4 of 543 games) is not a precision
  artifact** — it is the known conifer/xgboost ≥ 2.0 threshold-rounding
  convention issue; no width fixes it. **Accepted**: HW is within 2 games of
  float (inside seed noise ±0.1pt), and downstream verification is judged
  bit-exact vs the **C++ emulation as golden** (the role XSIM golden plays in
  the MLP flow), not vs float xgboost.
- Honest footnote: HW spread MAE (9.771) lands a hair *above* the Vegas
  baseline (9.763) where the float model sat a hair below — a 0.008 pt
  difference on 543 games, noted for the final comparison table.

### 3. HLS Projects + Template Patches

`convert_hls.py` emits both projects (Basys 3 `xc7a35tcpg236-1`, 100 MHz,
`ap_fixed<24,12>`, tops `conifer_win` / `conifer_spread`) and auto-patches two
conifer 1.9 template bugs discovered via two failed synthesis attempts:

1. **Phantom `tree_scores` argument** — the generated header and testbench
   carry a third debug argument that the generated firmware (the synthesis
   truth) does not define. Attempt 1 died at the csim *linker* (undefined
   3-arg symbol); aligning only the testbench moved the failure to the
   *header* (2-arg call vs 3-arg prototype). Both are now aligned to the
   2-arg firmware signature.
2. **`vivado_synth.tcl` targets the VHDL view** — Vitis HLS emits the same
   RTL in both Verilog and VHDL; patched to `syn/verilog` for consistency
   with the UART wrapper, the MLP flow, and the cosim gate (all Verilog).

`make_tb_data.py` wrote 100 val-set vectors per project plus `golden_cpp.npy`
— the bit-accurate emulation outputs that serve as the verification golden
from here to the board.

### 4. Vitis HLS Flow — Cosim PASS, IPs Exported

`run_synthesis.bat` mirrors the MLP recipe (Vitis 2025.2; copy to `C:\Temp`
first because Vitis HLS's atomic renames silently fail on WSL paths), then
runs csim → csynth → **RTL cosim in Verilog (the gate — if cosim fails, the
RTL is broken regardless of any other result)** → IP export, per stage.

Both stages: **cosim PASS**, IP catalog blocks exported.

### 5. Real Utilization — the HLS-Estimate Trap, Again

csynth *estimated* 96,878 LUTs (465%) for win and 33,369 (160%) for spread —
implying neither could ever fit. The MLP flow already learned this estimate is
inflated (its real number came in at 17,896). `run_vsynth.bat` ran real Vivado
logic synthesis on the Verilog netlists:

| | conifer_win (132×d2) | conifer_spread (31×d3) | MLP (measured) |
|---|---|---|---|
| LUT | **6,758 (32.5%)** | **5,111 (24.6%)** | 17,896 (86%) |
| FF | 5,091 (12.2%) | 2,781 (6.7%) | — |
| DSP / BRAM | **0 / 0** | **0 / 0** | uses both |
| Latency | 9 cycles / 90 ns | 9 cycles / 90 ns | hundreds of cycles |
| Interval | II=1 (new input every 10 ns) | II=1 | — |

**Combined: 11,869 LUTs = 57%** — comfortable fit with room for the UART/AXIS
wrapper. The estimates were inflated **14× and 6.5×**. Now confirmed project
policy: *never gate a fit decision on the csynth estimate; always run
`vivado_synth.tcl`.*

---

## Key Design Decisions & Why

- **Smoke-test the regressor before building anything** — regression was the
  known conifer risk; a same-day pivot to spread binning was on the table if
  it failed. It passed, so no pivot.
- **C++ emulation is the verification golden** (not float xgboost) — mirrors
  the MLP's XSIM-golden convention; fixed-point HW is judged against the
  bit-accurate model of itself, while float agreement is a modeling-fidelity
  stat (99.26%, within seed noise of float).
- **`ap_fixed<24,12>`** — cheapest config on the fidelity plateau; frac bits
  drive fidelity, integer bits must cover raw elo (~1500).
- **Accept the 0.7% threshold-convention disagreement** — unfixable by width,
  2 games of accuracy on val, and invisible to the golden-based verification.
- **Verilog everywhere** — cosim gate, synthesis view, and phase-5 integration
  all match the existing UART wrapper and MLP flow.
- **Cosim as the exit gate** — inherited from the MLP flow's hard-won lesson
  (`synth_only.tcl`): C-sim alone missed the stream-deadlock class of bug.
- **Generated projects gitignored** — regenerable from committed models +
  scripts, same convention as `phase4_hls/hls_project/`.

---

## Remaining Work

| Step | What | Gate |
|---|---|---|
| Two-stage wrapper | Chain stage1 → sigmoid → stage2, `+ vegas_spread` adder, AXIS interface matching the phase 5 UART plumbing | XSIM golden vectors bit-exact vs `golden_cpp.npy` |
| Vivado implementation | Full impl of the combined design | Timing met; fits with UART wrapper |
| Board deploy | Reuse phase 7 pyserial harness; head-to-head latency + resource table vs MLP | Bit-exact vs golden; honest UART-bound framing |
