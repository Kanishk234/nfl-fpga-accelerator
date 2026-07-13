# Phase 4 Complete: HLS Conversion

## What Phase 4 Does

Converts the trained Keras model (`artifacts/model_best.keras`) into synthesizable
HLS C++ using hls4ml 1.3.0, then runs Vitis HLS 2025.2 to produce:
- Resource utilization estimates (LUT, FF, BRAM, DSP)
- RTL (Verilog/VHDL) for the neural network
- A Vivado IP catalog block for use in Phase 5

The target is an Artix-7 XC7A35T (Basys 3) with a budget of:
- LUT  ≤ 20,800
- FF   ≤ 41,600
- BRAM ≤ 100
- DSP  ≤ 90

**Original result (Run 6): 18,921 LUT (91%), 14,184 FF (34%), 14 BRAM (14%), 18 DSP (20%).**  
**After precision tweak (Run 7): 16,234 LUT (78%), 11,539 FF (27%), 14 BRAM (14%), 18 DSP (20%). All within budget.**

---

## Architecture

### Why hls4ml can't directly convert the QKeras model

hls4ml 1.3.0 has two incompatibilities with QKeras under Keras 3:
- `keras.models.load_model()` fails to deserialize QKeras quantizers
- hls4ml's `keras_v3` converter asserts activation is a `FunctionType`, but `QActivation`
  holds a quantizer object, not a function

**Fix:** Build the QKeras architecture from scratch (no deserialization), load QAT weights,
apply kernel/bias quantizers to snap weights to the fixed-point grid, then transfer those
to a plain `Dense+ReLU` inference model. hls4ml sees a clean Keras model; the precision
config mirrors the Phase 3 quantizers exactly.

### Model structure going into hls4ml

```
Input (21 features, ap_fixed<18,6>)   ← Run 7: narrowed from ap_fixed<24,10>
  → Dense 128  (weights ap_fixed<8,1>, bias ap_fixed<16,7>)  → ReLU (ap_fixed<8,4>)
  → Dense 64   (weights ap_fixed<8,1>, bias ap_fixed<16,7>)  → ReLU (ap_fixed<8,4>)
  → Dense 32   (weights ap_fixed<8,1>, bias ap_fixed<16,7>)  → ReLU (ap_fixed<8,4>)
  → Dense 1    (win probability)   → Sigmoid  → layer9_out  (ap_fixed<18,6>, 18-bit)
  → Dense 1    (point spread)      → linear   → layer10_out (ap_fixed<32,16>, 32-bit)
```

### hls4ml config

- Backend: `'Vitis'` (Vivado HLS was discontinued after 2020.1)
- Strategy: `'Resource'` → emits `dense_resource` (serialized MACs over RF cycles).
  Without this, hls4ml uses `dense_latency` which unrolls ALL multiplications in one
  cycle — 8,192 parallel MACs for dense_2 alone → ~400,000 LUT.
- ReuseFactor per layer (final): `dense_1=168, dense_2=512, dense_3=512, win/spread=32`
- io_type: `'io_serial'` (see synthesis run history below)

---

## Synthesis Run History (6 runs to get under budget)

### Run 1 — First synthesis, BRAM pragma broken

**Config:** io_parallel, RF=168/128/128, parallel relu  
**Result:** LUT=398,705 (1917%), FF=189,972

**Root cause:** `#pragma HLS RESOURCE variable=weights core=ROM_nP_BRAM` is **silently
ignored** in Vitis HLS 2025.2. That pragma was deprecated in 2020.2. Without it, the
21×128=2,688 weights for dense_1 synthesized as distributed LUT RAM (16,000+ LUT for
weights alone, multiplied across all layers).

**Fix applied:** Replaced with the 2025.2 syntax in `nnet_dense_resource.h`:
```cpp
// OLD (deprecated, ignored):
#pragma HLS RESOURCE variable=weights core=ROM_nP_BRAM
// NEW:
#pragma HLS bind_storage variable=weights type=rom_np impl=bram
```
Applied to both `rf_leq_nin` and `rf_gt_nin_rem0` variants. Also patched the hls4ml
library file (`venv/.../nnet_dense_resource.h`) so regenerating the project preserves it.

---

### Run 2 — BRAM fixed, baseline established

**Config:** io_serial, RF=168/128/128, parallel relu  
**Result:** LUT=30,850 (148%), FF=19,286, BRAM=26, DSP=18, Fmax=93.46 MHz

- BRAM fix worked: weights now in block ROM
- Still over budget; relu layers consumed **8,736 LUT** (128+64+32 parallel comparators)

**Root cause of relu LUT:** `nnet_activation.h` had `#pragma HLS PIPELINE` at the
**function level**, which causes HLS to fully unroll the inner loop. For relu_config3
(128 elements) this means 128 parallel comparators = 4,992 LUT for that layer alone.

---

### Run 3 — RF tuning for dense layers

**Config:** io_serial, RF=336/512/512, parallel relu  
**Result:** LUT=27,921 (134%), FF=25,906, BRAM=13, DSP=10, Fmax=83.73 MHz

- Increasing RF for dense_2 and dense_3 from 128→512 reduced their block_factor
  (parallel MACs per cycle) from 64 to 16, significantly reducing logic
- BUT increasing dense_1 RF from 168→336 made it **worse**: LUT went from ~6,722 to
  ~9,117 because `rf_gt_nin_rem0` variant's `sparsemux` scales with RF
  (went from `sparsemux_17_3_24_1_1` to `sparsemux_33_4_24_1_1`)
- Relu still 8,736 LUT (parallel)

---

### Run 4 — Serialized relu fix applied

**Config:** io_serial, RF=168/512/512, serial relu  
**Result:** LUT=27,649 (133%), FF=28,696, BRAM=14, DSP=18, Fmax=97.53 MHz

**Relu fix:** Changed `nnet_activation.h` to put `#pragma HLS PIPELINE II=1` **inside**
the for loop instead of at the function level:
```cpp
// OLD — function-level PIPELINE forces HLS to unroll the inner loop completely:
template <...> void relu(...) {
    #pragma HLS PIPELINE    // <-- here; 128 parallel comparators = 4,992 LUT
    for (int ii = 0; ii < CONFIG_T::n_in; ii++) {
        #pragma HLS UNROLL
        ...
    }
}

// NEW — loop-level PIPELINE II=1, one comparator shared sequentially:
template <...> void relu(...) {
    // No pragma here
    for (int ii = 0; ii < CONFIG_T::n_in; ii++) {
        #pragma HLS PIPELINE II=1   // <-- inside loop; 1 comparator, ~128 LUT total
        ...
    }
}
```
Relu LUT dropped from 8,736 → ~379 total. Saving of 8,357 LUT.

**New problem discovered:** dense_1 LUT jumped from ~6,722 → 14,342 (doubled).

**Root cause (STREAM/UNROLL conflict):** With `io_serial`, intermediate arrays become
`hls::stream<>` FIFOs with `#pragma HLS STREAM depth=2`. The dense Result loop had:
```cpp
Result:
    for (int ires = 0; ires < CONFIG_T::n_out; ires++) {
        #pragma HLS UNROLL
        res[ires] = cast<data_T, res_T, CONFIG_T>(acc[ires]);
    }
```
`UNROLL` tries to write all 128 outputs simultaneously. A stream only accepts one write
per cycle. HLS generates a 128-to-2 drain mux — a massive barrel-shifter state machine —
to resolve the conflict. This added ~7,620 LUT to dense_1 alone.

Net effect: relu saving (−8,357) almost entirely eaten by stream conflict (+7,620).
Total went from 27,921 → 27,649 (only −272 LUT).

---

### Run 5 — Tried io_parallel to avoid stream conflict

**Config:** io_parallel, RF=168/512/512, serial relu  
**Result:** LUT=65,148 (313%), FF=78,009, BRAM=14, DSP=18

**What happened:** `io_parallel` passes arrays between layers instead of streams. HLS
auto-detected a **DATAFLOW** pattern (sequential sub-function calls, each array written
by exactly one producer and read by exactly one consumer) and applied it automatically.

DATAFLOW creates a separate pipeline process for each layer and inserts FIFOs between
them. Our 128/64/32-element inter-layer arrays, with DATAFLOW's default FIFO sizing,
produced:

```
FIFO overhead: 32,708 LUT and 47,619 FF
```

Even without FIFO overhead, Instance LUT = 32,343 (already 155% of budget).

**io_parallel cannot work for this model.** DATAFLOW FIFOs make it catastrophic, and
even without DATAFLOW the instance logic alone would exceed budget.

---

### Run 6 — io_serial + fix the UNROLL/STREAM conflict ✓

**Config:** io_serial, RF=168/512/512, serial relu, PIPELINE II=1 Result loop  
**Result: LUT=18,921 (91%), FF=14,184 (34%), BRAM=14 (14%), DSP=18 (20%)**

**Fix:** Changed all three `dense_resource` Result loops from `UNROLL` to `PIPELINE II=1`:
```cpp
Result:
    for (int ires = 0; ires < CONFIG_T::n_out; ires++) {
        // PIPELINE II=1 instead of UNROLL:
        // With io_serial, res[] is an hls::stream<>. UNROLL tries to write all n_out
        // elements simultaneously but streams only allow 1 write/cycle — HLS generates
        // a huge n_out-to-depth drain mux (~7,620 extra LUT for dense_1).
        // PIPELINE II=1 writes one element per cycle: simple counter, ~50 LUT.
        #pragma HLS PIPELINE II=1
        res[ires] = cast<data_T, res_T, CONFIG_T>(acc[ires]);
    }
```

HLS correctly extracted the Result loop as a separate sub-module
(`Pipeline_Result`) with a sparsemux for sequentially reading the partitioned
accumulator array — but the total LUT was far lower because:
- No drain-mux explosion from the UNROLL/STREAM conflict
- 7 inter-layer FIFOs implemented as 24-bit depth-2 shift registers: only 476 LUT total
  (vs 32,708 in Run 5's DATAFLOW)

---

### Run 7 — Global precision narrowed from fixed\<24,10\> to fixed\<18,6\> ✓

**Config:** io_serial, RF=168/512/512, serial relu, PIPELINE Result, **global default fixed\<18,6\>**  
**Result: LUT=16,234 (78%), FF=11,539 (27%), BRAM=14 (14%), DSP=18 (20%)**

**Motivation:** The `fixed<24,10>` global default (14 fractional bits) had 2 bits more
precision than needed. The safety margin for 128-step accumulation error is:
- Need ≥12 fractional bits so that 128 × 2^-12 = 0.031 < 0.05 (mean delta threshold)
- `fixed<24,10>` (14 frac bits) → error ≈ 0.008 — more headroom than required
- `fixed<18,6>` (12 frac bits) → error ≈ 0.031 — within threshold, saves 6 bits/accumulator

**C simulation result:** mean delta = 0.0473, max delta = 0.0964 — both within tolerance
(thresholds: mean < 0.05, max < 0.10).

**LUT savings by layer:**

| Layer | Run 6 LUT | Run 7 LUT | Saved |
|-------|-----------|-----------|-------|
| dense_1 (RF=168) | 9,659 | 7,963 | −1,696 |
| dense_2 (RF=512) | 5,119 | 4,447 | −672 |
| dense_3 (RF=512) | 2,484 | 2,220 | −264 |
| win/spread/sigmoid | ~580 | 544 | −36 |
| FIFOs | 476 | 476 | 0 |
| Mux/Reg | 603 | 584 | −19 |
| **Total** | **18,921** | **16,234** | **−2,687** |

**Side effect — Fmax estimate dropped:** 97.53 MHz → 94.62 MHz (HLS estimate). The
critical path is the `sparsemux → select → phi` chain in dense_1's ReuseLoop — the 18-bit
operands have slightly more delay than 24-bit paths after synthesis restructuring. This is
an HLS pre-route estimate; Vivado implementation typically closes 100 MHz on these paths.
Watch the timing report in Phase 5's full Vivado synthesis.

**Side effect — output port widths changed:** With the narrower global default, hls4ml
inferred different output types. The `auto` precision on win and spread output layers
resolved to:
- `layer9_out` — 18-bit (`ap_fixed<18,6>`) with `layer9_out_ap_vld`
- `layer10_out` — 32-bit (`ap_fixed<32,16>`) with `layer10_out_ap_vld`

Port names also lost the `_0_V` suffix that the Phase 5 plan originally assumed. The
`features_q0` input is now 18-bit (was 8-bit). See Phase 5 Prerequisites below.

---

## Final Resource Breakdown

Run 7 (current, fixed\<18,6\> global default):

```
+-----------------+---------+----+-------+-------+
|      Name       | BRAM_18K| DSP|   FF  |  LUT  |
+-----------------+---------+----+-------+-------+
| dense_1 (RF=168)|        4|  16|   5796|   7963|
| dense_2 (RF=512)|        5|   0|   3064|   4447|
| dense_3 (RF=512)|        2|   0|   1530|   2220|
| win   (RF=32)   |        1|   1|    134|    177|
| spread (RF=32)  |        1|   1|    154|    175|
| relu_1          |        0|   0|     21|    122|
| relu_2          |        0|   0|     20|    120|
| relu_3          |        0|   0|     19|    119|
| sigmoid         |        1|   0|     81|    192|
| FIFOs (7×d2)    |        -|   -|    693|    476|
| Mux/Reg         |        -|   -|     27|    223|
+-----------------+---------+----+-------+-------+
| Total           |       14|  18|  11539|  16234|
| Budget          |      100|  90|  41600|  20800|
| Utilization     |     14% | 20%|    27%|    78%|
+-----------------+---------+----+-------+-------+
```

Latency: 1,758–1,760 cycles ≈ 17.6 µs per inference at 100 MHz  
Estimated Fmax: 94.62 MHz (HLS pre-route; post-route in Phase 5 Vivado expected to close 100 MHz)  
Headroom for Phase 5 UART wrapper: 20,800 − 16,234 = **4,566 LUT**

---

## Files

| File | Purpose |
|------|---------|
| `convert.py` | Main entry point: loads model, builds hls4ml config, runs C-sim |
| `resource_report.py` | Parses csynth.rpt after Windows synthesis, updates artifact JSON |
| `run_synthesis.bat` | Windows batch: copies project to C:\Temp, runs vitis-run, copies back |
| `synth_only.tcl` | Tcl script called by vitis-run to open project and run csynth_design |
| `patches/nnet_dense_resource.h` | Patched hls4ml template (BRAM pragma + PIPELINE Result) |
| `patches/nnet_activation.h` | Patched hls4ml template (loop-level PIPELINE for relu) |
| `hls_project/` | Generated HLS project (gitignored — regenerate with convert.py) |

### Applying patches to a fresh venv

If the venv is recreated, re-apply the patches:
```bash
TMPL=venv/lib/python3.12/site-packages/hls4ml/templates/vivado/nnet_utils
cp phase4_hls/patches/nnet_dense_resource.h $TMPL/
cp phase4_hls/patches/nnet_activation.h     $TMPL/
```
Then re-run `python3 phase4_hls/convert.py`.

---

## Key Lessons

| Symptom | Root cause | Fix |
|---------|-----------|-----|
| LUT=398k (Run 1) | `#pragma HLS RESOURCE` silently ignored in Vitis 2025.2 | `#pragma HLS bind_storage type=rom_np impl=bram` |
| Relu = 8,736 LUT | `#pragma HLS PIPELINE` at function level unrolls inner loop | Move pragma inside the loop |
| dense_1 = 14,342 LUT (Run 4) | `#pragma HLS UNROLL` on stream writes generates drain mux | `#pragma HLS PIPELINE II=1` on Result loop |
| LUT=65,148 (Run 5) | `io_parallel` triggers HLS auto-DATAFLOW → 32,708 LUT of FIFOs | Use `io_serial` |
| RF=336 worse than RF=168 | `rf_gt_nin_rem0` sparsemux grows with RF | Use smallest valid RF ≥ n_in |

---

## Phase 5 Prerequisites

- IP zip: `phase4_hls/hls_project/myproject_prj/solution1/impl/ip/xilinx_com_hls_myproject_1_0.zip`
  - Regenerate with: `run_synthesis.bat` on Windows (copies to C:\Temp, runs vitis-run, copies back)

### Actual MLP IP port list (verified from csynth.rpt, Run 7)

```
ap_clk              in   1   ap_ctrl_hs
ap_rst              in   1   ap_ctrl_hs
ap_start            in   1   ap_ctrl_hs
ap_done             out  1   ap_ctrl_hs
ap_idle             out  1   ap_ctrl_hs
ap_ready            out  1   ap_ctrl_hs
features_address0   out  5   ap_memory    — which of the 21 features the MLP is requesting
features_ce0        out  1   ap_memory    — chip enable: high when MLP wants a feature
features_q0         in  18   ap_memory    — feature value (ap_fixed<18,6>), NOT 8-bit
layer9_out          out 18   ap_vld       — win probability (ap_fixed<18,6>)
layer9_out_ap_vld   out  1   ap_vld
layer10_out         out 32   ap_vld       — point spread (ap_fixed<32,16>)
layer10_out_ap_vld  out  1   ap_vld
```

**Key differences from the original Phase 5 plan:**
- Port names do NOT have the `_0_V` suffix (plan used `layer9_out_0_V` etc.) — use the names above
- `features_q0` is **18-bit**, not 8-bit — mlp_controller must zero-extend the received byte:
  `features_q0 = {{6{1'b0}}, byte_val, 4'b0}` (places byte in high fractional bits of fixed<18,6>,
  approximating byte/256 ≈ byte/255, 0.4% error)
- `layer9_out` is **18-bit** — to encode as uint8 response: take bits [11:4] (top 8 fractional bits)
- `layer10_out` is **32-bit** — to encode as int8 response: take bits [23:16] (integer byte of fixed<32,16>)

### Timing flag
HLS estimated Fmax = 94.62 MHz (below the 100 MHz target). This is a pre-route estimate.
If Phase 5 Vivado implementation reports negative WNS on the `sparsemux → select → phi` path
in dense_1's ReuseLoop, add one pipeline register there in `mlp_controller.v`.

- No board needed for Phase 5 — Vivado synthesis runs entirely on Windows
- Board (Basys 3) is needed only for Phase 7 (bitstream upload + UART testing)

---

## ADDENDUM (2026-06-17) — Re-synthesized with io_stream to fix in-hardware deadlock

The original `io_serial` IP documented above **deadlocked on real silicon** (see
AUDIT_REPORT.md §1): a strictly sequential top-level FSM with depth-2 inter-layer
FIFOs whose producer/consumer never ran concurrently, plus a dual-consumer
`layer7_out` stream that was re-read destructively. Phase 6 only passed because the
cocotb Makefile substituted deep/replay FIFO stubs that were never in the bitstream;
RTL cosim was never run. This is what broke Phase 7 board bring-up.

**Fix:** switched `convert.py` to `io_type='io_stream'` — the maintained hls4ml path,
which emits a real `#pragma HLS DATAFLOW` region with rate-matched FIFOs and a
`clone_stream` that duplicates layer 7 into the two output heads.

**Verification gate added:** `synth_only.tcl` now runs `cosim_design -rtl verilog`
after csynth and aborts (no IP export) on failure. Cosim is the only test that
exercises the real RTL with real FIFOs — it would have caught the original deadlock.
`make_tb_data.py` generates 20 real validation vectors into `hls_project/tb_data/`,
and `firmware/weights` is staged so the cosim C model can load its `.txt` files.

**Result (Vitis HLS 2025.2, xc7a35tcpg236-1):**
- csynth + RTL cosim **PASS** — max `hls::stream` depth reached = 1 (no deadlock),
  RTL/C outputs match (sample: win 0.7178, spread 5.827). IP re-exported.
- C-sim delta unchanged (precision still `fixed<18,6>`): mean 0.0473, max 0.0964.
- HLS LUT estimate: **28,869 (138%)** — DSP 18/90, FF 24,690/41,600, BRAM 14/100.
  The HLS LUT estimate overcounts; the binding fit number is the **actual Vivado
  implementation** result measured in Phase 5, not this estimate. If real impl
  exceeds 20,800 LUT, trim via ReuseFactor → precision → dense_1 width (128→64).
- Est. Fmax still 94.62 MHz (timing watch carries forward to Phase 5).

### NEW top-level interface (io_stream — supersedes the io_serial ports above)
io_stream changes the interface to **AXI4-Stream + ap_ctrl_hs**. Verified from the
regenerated `myproject_prj/solution1/syn/verilog/myproject.v`:

| Port | Dir | Width | Meaning |
|---|---|---|---|
| `features_TDATA` | in | **[671:0]** (21 lanes × 32 bits) | all 21 features in ONE beat; each `ap_fixed<18,6>` in low 18 bits of its 32-bit lane |
| `features_TVALID` / `features_TREADY` | in / out | 1 | input stream handshake (push 1 beat) |
| `layer9_out_TDATA` | out | [31:0] | win; value in bits **[17:0]** as `ap_fixed<18,6>` |
| `layer10_out_TDATA` | out | [31:0] | spread; full 32 bits as `ap_fixed<32,16>` |
| `layer9_out_TVALID/TREADY`, `layer10_out_TVALID/TREADY` | out/in | 1 | output stream handshakes (pop) |
| `ap_clk` | in | 1 | 100 MHz |
| `ap_rst_n` | in | 1 | **ACTIVE-LOW** (invert the existing active-high `rst`) |
| `ap_start` / `ap_done` / `ap_ready` / `ap_idle` | — | 1 | block-level `ap_ctrl_hs` handshake |

**Supersedes the io_serial notes above:** the old `features_address0/ce0/q0` ap_memory
interface, the `layer9_out[11:4]` win slice, and the `layer10_out[23:16]` spread slice
are OBSOLETE. The byte→fixed trick still applies but now **per 32-bit lane**:
`lane[17:0] = {6'b0, byte, 4'b0}`. `mlp_controller.v` must be rewritten for this AXIS
handshake (read the real `myproject.v` ports — do not assume). Re-extract the new
`impl/ip/` export over `artifacts/ip_repo/` before Phase 5 synthesis; the old ip_repo
still contains the deadlocking io_serial IP.
