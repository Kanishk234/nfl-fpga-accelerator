# The Conifer Track — A Technical Account

> Companion to `PROJECT_DOCUMENT.md`, which covers the MLP track end to end.
> This one covers the **GBDT variant**: a stacked XGBoost model taken through
> the same seven phases to the same board, finishing at **100/100 bit-exact
> inference on silicon**. It deliberately does *not* re-explain UART framing,
> the Basys 3, temporal splits, or fixed-point basics — those are in the main
> doc and were reused wholesale. What follows is what was **different**, what
> **broke**, and what the comparison taught.

---

## 1. Why a second model at all

The MLP shipped and worked. The GBDT exists to answer a question the MLP alone
could not: *for tabular data on a small FPGA, is a neural network even the right
choice?* The prior says gradient-boosted trees dominate tabular problems, and
trees have a fundamentally different hardware profile — comparators and adders
instead of multiply-accumulates, so **no DSPs at all**.

To make the comparison honest it had to be 1:1: identical 21 features in the
identical locked order, identical temporal split (train ≤2020, val 2021–2022,
test 2023–2024), identical targets, identical board, identical baud rate.

The answer turned out to be unambiguous, and it is in §7.

### The `_conifer` convention

Phases 1 and 3 have no GBDT analog. Everything else lives in a **sibling folder**
so the MLP originals stay byte-identical and the comparison can never be accused
of being contaminated:

```
gbdt/phase2_train/  gbdt/phase4_hls/  gbdt/phase5_fpga/  gbdt/phase6_sim/  gbdt/phase7_deploy/
```

Where a file was genuinely unchanged it is **sourced, not copied** —
`uart_rx.v` / `uart_tx.v` are read straight out of `mlp/phase5_fpga/hdl/`, the host
`GameCatalog` is imported from `mlp/phase7_deploy/`, and `GBDTFeatureBuilder`
subclasses the MLP's `FeatureBuilder`. Exactly one MLP file was modified in the
whole track (`ui/index.html`, made model-driven rather than forked — §6).

---

## 2. What carried over, and what had to change

| Layer | MLP | GBDT | Why |
|---|---|---|---|
| Feature scaling | MinMaxScaler → INT8 | **none, raw features** | trees split on raw thresholds; scaling is a no-op |
| Quantization phase | phase 3 (QKeras) | **doesn't exist** | absorbed into conifer's threshold quantization at phase 4 |
| Precision | `ap_fixed<18,6>` | `ap_fixed<24,12>` | integer bits must cover raw `elo ≈ 1500` |
| HLS tool | hls4ml | **conifer** | hls4ml's sibling project for BDTs |
| IP interface | AXI-Stream (after a deadlock rewrite) | `ap_ctrl_hs` + parallel ports | trees need no streaming; the entire deadlock bug class vanishes |
| Compute resource | 18 DSPs | **0 DSPs** | comparators + adders only |
| Model structure | shared trunk, dual heads | **two chained stages** | stage 2 consumes stage 1's win probability |
| Sigmoid | inside the network | **1024×12 hardware ROM** | new HDL with no Python counterpart |
| UART request | 23 B (1 byte/feature) | **65 B** (3 bytes/feature) | raw `elo` cannot fit in a byte |
| UART response | 4 B (quantized) | **8 B** (full-width) | enables zero-tolerance verification |
| Verification gate | ±2 counts of slack | **bit-exact, zero tolerance** | full-width results make exactness achievable |

That last row is the through-line of the whole track. Sending 42 extra bytes per
request buys the ability to demand *exact* equality between Python, simulation
and silicon — and every bug in §5 was caught by that demand.

---

## 3. Phase 2 — the model

Stacked XGBoost: stage 1 predicts win probability, stage 2 takes the 21 features
**plus that probability** and predicts the *residual to the Vegas line*; the line
is added back at the end (one adder on the FPGA).

Two decisions did the heavy lifting:

- **Residual parameterization.** The naive "predict raw spread" model scored MAE
  9.87 — *worse than the Vegas line itself* (9.76). A 27-config sweep found that
  **every single config lost to the line**. The reason is structural: game margin
  is nearly linear in the Vegas line, and trees approximate a line as a
  high-variance staircase, adding error. Reframed as a residual, the model
  early-stops near zero trees — its own verdict being *"the best correction to
  the line is no correction."* Spread is a **data noise floor**, not a modeling
  failure, and it bounds both models equally.
- **Monotone constraints + depth 2.** Forcing win probability non-decreasing in
  home-strength signals bought ~+1pt accuracy on 543 val games, and depth-2 trees
  dominated the sweep. A hardware bonus fell out: depth 2 halves the comparator
  depth per tree, so the *more accurate* model is also the *cheaper* one.

**Out-of-fold stacking** is the correctness detail that makes it valid — the
win-prob feature used in training comes from 5-fold models that never saw the
fold, otherwise the spread model trains on leaked, overconfident probabilities.

Result: **65.75% val / 70.22% test** win accuracy, AUC 0.716 / 0.731, spread MAE
9.758 — beating the MLP on val accuracy, both AUCs, and test spread MAE, tying it
on test accuracy.

---

## 4. Phase 4 — conifer, and the estimate trap

**The risk that shaped the phase:** conifer grew out of high-energy-physics
trigger *classification*; regression support was version-dependent and might
simply not work. A same-day pivot to spread binning was on the table. So nothing
was built until a smoke test converted both models through conifer's C++ backend.
Both passed — no pivot.

**Precision scan → `ap_fixed<24,12>`.** Ten configs swept. The finding worth
keeping: **fractional bits are the only controlling variable** — configs with
equal frac bits give byte-identical results regardless of total width, and
fidelity plateaus at frac=12. `<24,12>` is the cheapest point on the plateau.

**The HLS estimate trap, hit a second time.** csynth *estimated* 96,878 LUTs
(465%) for the win stage and 33,369 (160%) for spread — implying neither could
ever fit on the board. Real Vivado synthesis:

| | conifer_win (132×d2) | conifer_spread (31×d3) | MLP |
|---|---|---|---|
| LUT | 6,758 (32.5%) | 5,111 (24.6%) | 17,896 (86%) |
| DSP / BRAM | **0 / 0** | **0 / 0** | uses both |
| Latency | 9 cycles | 9 cycles | ~1,500 cycles |

The estimates were inflated **14× and 6.5×**. The MLP track had already learned
this once; it is now project policy: *never gate a fit decision on the csynth
estimate.*

**Accepted, not fixed:** a residual 0.7% decision disagreement vs float xgboost
(4 of 543 games) traced to conifer's threshold-rounding convention with xgboost
≥2.0. No bit width fixes it. It is accepted because the verification golden is
the **C++ emulation**, not float xgboost — fixed-point hardware is judged against
a bit-accurate model of itself, exactly as the MLP is judged against XSIM.

---

## 5. Phases 5–6 — the chain, and three 1-ulp bugs

The board runs something Python never did: stage 1's *fixed-point margin* through
a *hardware sigmoid ROM* into stage 2. Phase 4's goldens fed stage 2 a **float**
probability, so they could not verify the chain.

`make_chain_golden.py` models the full path bit-for-bit and — importantly — **is
the sigmoid spec**. It emits `sigmoid_lut.mem`, which `sigmoid_rom.v` loads with
`$readmemh`. Model and ROM cannot drift, because there is only one artifact.
(Midpoint sampling, `ROM[i] = round(sigmoid(-8 + (i+0.5)/64) · 4096)`, halves the
worst-case error for free.) Chaining cost nothing: **65.38%** accuracy, MAE
**9.7697**, worst-case sigmoid error 0.033, and the observed margin range
[−1.68, +2.25] never comes near the ROM's ±8 clamp.

Verification ran at two levels, both on the **real synthesized netlists**:
100-game chain regression (**100/100 bit-exact, 0 timeouts, 22 cycles/game**) and
a full-UART test driving the entire `top_gbdt` at 115200 baud, including a
corrupted checksum and an SOF-collision packet.

Implementation: **12,095 LUTs (58%), 18,956 FF, 0 DSPs, WNS +0.965 ns** — the
UART wrapper costs just 226 LUTs over the bare IPs.

### The bug that defined the track

The chain golden was originally computed on **raw float features**. It disagreed
with XSIM by exactly 1 ulp on a handful of games. Cause: the conifer C++
emulation **truncates** (AP_TRN) where the host **rounds**. For any feature in
the upper half of a quantization step the emulation saw a value one step low, a
tree took a different branch, and the output moved by a whole leaf value.

Fix: pre-quantize the golden's inputs to `round(x·4096)` — the exact words the
UART carries.

This bug is the reason the zero-tolerance gate exists. A ±3 tolerance band would
have swallowed it silently, and it would have resurfaced on hardware as
"mysterious" occasional mismatches with no way to tell a golden bug from an RTL
bug from a host bug.

**It then happened twice more, and both times the gate caught it:**

| Where | Symptom | Cause |
|---|---|---|
| Chain golden (phase 6) | 1-ulp mismatch, some games | C++ emulation truncates, host rounds |
| Host encoder (phase 7) | **48/100 games** off by 1 LSB | golden casts to **float32** first; host used float64 |
| — | — | `elo ≈ 1522` exceeds float32's ~7 digits, straddling the rounding boundary |

The float32 cast is not incidental: the models were *trained* on float32, so it
is the canonical value a feature has. Both fixes are pinned by tests.

### Other things that bit

- **`ap_rst` is active-HIGH** on the conifer IPs (hls4ml's was `ap_rst_n`). Get it
  wrong and the IP never leaves idle — which reads as a controller bug.
- **`score_1` is a dead template input port**, never read internally. Tied to 0.
- **conifer 1.9 ships broken templates**: a phantom `tree_scores` argument that
  the firmware doesn't define (killed csim at the *linker*), and a synthesis tcl
  pointing at the VHDL view. Both auto-patched by `convert_hls.py`.
- **The pip wheel ships without its C++ headers** — `compile()` fails until
  nlohmann/json and Xilinx `ap_types` are dropped into `site-packages/conifer/external/`.
- A **false alarm worth recording**: the regressor appeared to crash on xgboost
  3.x's bracketed `base_score` (`'[-0.27]'`). conifer parses it fine — the bare
  `float()` was in *our own* test harness.

---

## 6. Phase 7 — deployment, and one shared page

Host side mirrors the MLP's split-across-the-boundary trick (pandas lives in WSL,
the COM port lives on Windows), with one addition: the exporter emits a
**self-checking validation bundle**. It re-encodes the 100 vectors from the
parquet and asserts they equal `tb_inputs.mem`, and cross-checks
`sim_results.csv` against the golden — so phase 6's "XSIM is bit-exact" claim is
re-verified every time the bundle is rebuilt, and the Windows-side tests need
only `pyserial`.

**The UI was not forked.** Rather than copy 280 lines of near-identical HTML,
`mlp/phase7_deploy/ui/index.html` became model-driven: the header badge, pipeline
labels, spread precision and raw-word denominator all arrive in a `model` block
from `/api/bootstrap`. Both servers now return a byte-identical page while
reporting different models. This is the only MLP file the track modified, and the
MLP's 22 tests still pass.

**First contact returned nothing** — and the diagnostic mattered more than the
fix. Four probes (correct 65-byte packet, a 23-byte MLP packet, 64 bytes of
noise, and a 2-second passive listen) were **all silent**. That distinguishes the
cases: a board running the *MLP* bitstream would have NACK'd the 65-byte packet,
because `0xAA` + 21 bytes + a wrong 23rd byte is a well-formed-but-wrong MLP
frame. Total silence means nothing is driving TX — an unprogrammed board. It was.
Worth recording because "no response" is the same symptom as the UART pin
ambiguity that cost the MLP track real debugging time; this pattern separates
*unprogrammed* from *miswired* from *wrong bitstream* in one shot.

Once programmed, everything passed on the first try.

---

## 7. Results

### On the board (2026-07-25, COM8)

```
games        : 100        bit-exact : 100/100
timeouts     : 0          bad status: 0
latency (ms) : min 6.6   median 6.9   max 8.2
```

Plus NACK (`status 0x01`), SOF-collision (63×`0xAA` treated as data), and clean
recovery afterwards — all verified on silicon.

**Four independent implementations of the same arithmetic agree bit-for-bit:**
the XGBoost/C++ emulation golden, the conifer HLS cosim, XSIM on the synthesized
netlists, and the chip.

### Proof it computes

Sweeping `elo_diff` from −300 to +300 with everything else pinned (inputs that
exist in no dataset) produces a **staircase, not a ramp** — 8 distinct levels
across 13 points with flat plateaus at both ends. That is the depth-2 tree
structure visible from outside the chip; a lookup table cannot produce it, and
the MLP's equivalent sweep moved continuously. It is also **monotonic**, meaning
the training-time monotone constraint survived XGBoost → conifer → HLS →
fixed-point → silicon intact.

### Head-to-head

| | GBDT | MLP |
|---|---|---|
| Val accuracy / AUC | **65.8% / 0.716** | 64.5% / 0.710 |
| Test accuracy / AUC | 70.2% / **0.731** | 70.2% / 0.724 |
| Spread MAE (test) | **9.776** | 9.86 |
| Board-vs-sim | **100/100 bit-exact, no tolerance** | 50/50 within ±2 counts |
| Inference latency | **22 cycles (0.22 µs)** | ~1,500 cycles |
| Slice LUTs | **12,095 (58%)** | 17,896 (86%) |
| DSPs | **0** | 18 |
| WNS | **+0.965 ns** | +0.145 ns |
| Round-trip | 6.9 ms | 3.4 ms |

The GBDT is more accurate, **68× faster** to compute, uses a third fewer LUTs, no
DSPs at all, and has 6.7× the timing slack. Its single loss is wall-clock
round-trip — and that is a *protocol* choice (full-width results), not a property
of the model. At 115200 baud the 65-byte request costs 6.34 ms of line time,
which is the floor; compute is 0.00022 ms. Raising the baud rate to 1 Mbaud would
take it to ~1 ms, and is documented but not taken.

Note the ranking flip: on the MLP, a 16 ms USB-bridge default dominated and was
fixed to reach 3.4 ms. Here compute is so small that the protocol's own line time
is the leading term.

---

## 8. What the track actually established

1. **For this problem, trees beat the network on every axis that matters.**
   More accurate, dramatically cheaper, dramatically faster, no DSPs. The
   tabular-data prior held under equal tuning effort.
2. **Cheap hardware and good accuracy were not in tension.** Depth-2 trees won
   the tuning sweep *and* halved comparator depth. The monotone constraints that
   bought accuracy also guarantee the hardware can never emit a probability that
   moves the wrong way with elo.
3. **Zero-tolerance verification is worth designing for.** It cost 42 bytes per
   packet and caught three separate 1-ulp bugs that a tolerance band would have
   hidden — two of them before hardware existed, one of them 48 games wide.
4. **Spread is a noise floor, not a failure.** The Vegas line already prices in
   everything the 21 features contain; both models tie it and neither beats it.
5. **Never trust the HLS estimate.** Inflated 14× here after the MLP track had
   already been burned once.

### Where it's weakest

- The **0.7% float-vs-fixed disagreement** is accepted, not solved. It's invisible
  to golden-based verification and inside seed noise, but it is a real modeling
  delta.
- **Board tests are manual** — they need hardware, so they can't run in CI.
- The two `_conifer` tracks still need **merging into the main tree**; the
  sibling-folder convention was always meant to end at board deploy.
- Round-trip is **line-time bound at 6.9 ms** and left there deliberately, which
  is defensible but means the headline latency number undersells the hardware by
  four orders of magnitude.
