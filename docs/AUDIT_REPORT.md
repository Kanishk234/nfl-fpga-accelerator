# Full Codebase Audit — NFL FPGA Accelerator

**Date:** 2026-06-11
**Scope:** Phases 1–7, all HDL, the synthesized IP Verilog actually in the bitstream
(`artifacts/mlp/ip_repo/hdl/verilog/`), the cocotb suite, deployment code, and all pytest suites.
**Status at time of audit:** `top.v` is a loopback stub (working tree), full design at `HEAD`
(`a842c7e`). Board debugging in progress — LD0 never lights on inference attempts.

---

## TL;DR

Three independent **blocking** problems. The suspected B18/A18 pin swap is **not** one of them.

| # | Finding | Severity |
|---|---|---|
| 1 | The synthesized MLP IP **deadlocks in real hardware by construction** — Phase 6's FIFO "fixes" were simulation-only stubs that mask a genuine RTL bug. This explains the original smoke-test timeout. | **BLOCKING** |
| 2 | `top.v` is a loopback stub; restoring the full design will collide with the LED pins now in the XDC | **BLOCKING** |
| 3 | The committed synthesis results (9,588 LUT, WNS +0.111) were measured on a netlist where the MLP input was **tied to ground** (pre-`features_q0`-fix). They are invalid; the fixed design may not fit or close timing. | **BLOCKING** |
| 4 | UART pins **B18=RX / A18=TX are correct** per the Digilent Basys 3 master XDC. Stop flipping them. | resolved question |

Predicted behavior of the current full design on the board: NACK test **passes** (framing layer
never touches the MLP), any valid inference **hangs forever** in `WAIT_DONE`, and all subsequent
packets are silently dropped because `ap_idle` never returns high. That matches the observed
history exactly.

---

## 1. BLOCKING — The MLP IP cannot complete an inference in hardware

### Evidence (from `artifacts/mlp/ip_repo/hdl/verilog/`, the exact source Vivado synthesized)

**a) The top-level FSM is strictly sequential, not dataflow.**
`myproject.v:67` declares an 18-state one-hot FSM. Each layer sub-module is started in its own
state and the *next* state blocks until the previous module's `ap_done`:

- dense_1 (`config2`) started in state 1 (`myproject.v:466`)
- state 2 blocks until config2 `ap_done` (`myproject.v:620-626`, transition at `833-835`)
- config2's **consumer** (relu loop `VITIS_LOOP_46_1`) is only started in state 3 (`myproject.v:550`)

**b) Depth-2 FIFOs sit between sequentially-executed producer/consumer pairs.**
`layer2_out_fifo_U` (`myproject.v:363`) is depth 2. dense_1 must push **128** results through it
before `ap_done` — but its consumer hasn't started. It writes 2 entries, the FIFO fills,
`ap_done` never fires, **state 2 blocks forever**. This is not an iverilog artifact; the silicon
behaves identically. The deadlock occurs on the *first layer* of the *first inference*.

**c) Even with infinite FIFO depth, the read/write counts don't balance.**
- `config4` (`..._config4_s.v`): `layer3_out_read` fires on every enabled pipeline iteration
  (`:2171`, gated only by `layer3_out_empty_n` at `:2470`), and the loop runs 512 iterations
  (`icmp … 9'd511` at `:2477`). The producer writes only **128** entries
  (`Pipeline_VITIS_LOOP_46_1` exits at `ii == 128`). 512 destructive reads of 128 writes.
- `config6`: same pattern — 512 reads of a 64-entry stream.
- `layer7_out` has **two consumers**: `config8` (`myproject.v:327`) and `config10`
  (`myproject.v:357`) both drain the single `layer7_out_fifo_U`. A FIFO read is destructive;
  the second head reads an empty stream.

**d) Phase 6 masked all of this.** The cocotb Makefile (`mlp/phase6_sim/cocotb/Makefile:11-27,83-94`)
*excludes the real FIFO modules* and substitutes `mlp_fifo_deep.v` (depth-256),
`mlp_fifo_dual_w8.v` (replay-once), and `mlp_fifo_replay_w8.v` (replay-4×/8×). None of these
exist in the bitstream. The PHASE6_COMPLETE justification — "in real hardware, producer and
consumer run concurrently so depth-2 is sufficient" — is wrong twice over: the generated FSM is
sequential (no concurrency), and no amount of concurrency can replay consumed FIFO data.

So the `test_mlp_verilog` PASS proves the **MAC arithmetic** is right, and nothing else. The
control structure that the bitstream actually contains was never simulated.

### Root cause

hls4ml `io_serial` (a deprecated, barely-maintained io_type) emits `hls::stream` channels between
layer function calls but does **not** wrap them in `#pragma HLS DATAFLOW`, and its
`dense_resource` rf_gt_nin template re-reads the input stream once per output partition instead
of buffering it locally. Vitis HLS happily schedules this (C-sim uses unbounded streams and a
different code path), and only **RTL co-simulation** would have caught it. Cosim was never run —
Phase 4 ran C-sim only.

### Fix (Phase 4 regeneration — required)

Pick one of these, in order of preference:

1. **Switch `io_type` to `io_stream`** in `mlp/phase4_hls/convert.py`. This is the modern, maintained
   path: hls4ml emits a proper DATAFLOW region with rate-matched FIFOs and stream-safe dense
   implementations. Keep `Strategy: Resource` and the per-layer ReuseFactors as a starting point.
2. **Keep `io_serial` but repair the templates** (you already maintain
   `mlp/phase4_hls/patches/nnet_dense_resource.h`):
   - At the top of each `dense_resource` variant, read the input stream exactly `n_in` times into
     a local `data_T buf[n_in]` array, then index `buf[]` inside the MAC loops. This fixes the
     512-reads-of-128 problem for config4/config6.
   - The dual-consumer `layer7_out` still needs splitting: in the generated `myproject.cpp`,
     duplicate the layer-7 stream (write each relu output into two streams, one per head) or
     buffer layer 7 into an array and pass the array to both heads.
   - This path is more invasive — only choose it if io_stream blows the LUT budget.

**Non-negotiable verification gate, whichever path you choose:**

```tcl
# in the Vitis HLS project, after csynth:
cosim_design -rtl verilog
```

Cosim runs the real RTL with the real FIFOs against the C testbench. If cosim hangs or fails,
the RTL is broken regardless of what any other test says. Make "cosim passes" a Phase 4 exit
criterion alongside the resource budget.

Then:
- Re-export the IP zip, re-extract to `artifacts/mlp/ip_repo/`, re-run Phase 5 synthesis.
- Re-run Phase 6 `test_mlp_verilog` **with the FIFO substitutions removed from the Makefile**.
  If the regenerated RTL still needs replay stubs to pass, it is still broken. The sim-only
  stubs should be deleted once the real RTL passes without them.
- Update `ACTUAL_PORT_WIDTHS.txt` (see §4) — port names/widths may change again with io_stream.

> **Heads-up:** `io_stream` changes the top-level interface. The `features` input will likely
> become an AXI-Stream-style port (`features_TDATA/TVALID/TREADY`) instead of the
> `ap_memory` (`address0/ce0/q0`) interface, and outputs may also become streams.
> `mlp_controller.v` will need rewriting for the new handshake — simpler, in fact: push 21
> values with TVALID/TREADY, pop two results. Budget time for this; don't try to keep the old
> controller.

---

## 2. BLOCKING — `top.v` restore and the XDC LED mismatch

- Working tree `top.v` is the loopback stub. The full design exists at `HEAD`
  (`git show HEAD:mlp/phase5_fpga/hdl/top.v`).
- The committed full `top.v` has **no `led` port**, but the current `basys3.xdc:13-19` constrains
  `led[0..2]` (U16/E19/U19). If you restore `top.v` verbatim, Vivado throws critical warnings for
  unmatched ports and you lose the debug LEDs.
- `git checkout` of the whole file set would also revert `basys3.xdc` and silently drop the LED
  pin constraints.

**Fix:** restore the full design *and* add the LED port with sticky debug semantics:

```verilog
module top (
    input  clk, input rst,
    input  uart_rxd, output uart_txd,
    output [2:0] led
);
    // ... existing wiring ...

    // Sticky debug LEDs — latch on first event, clear on rst.
    reg led_rx, led_err, led_res;
    always @(posedge clk) begin
        if (rst) begin
            led_rx  <= 1'b0; led_err <= 1'b0; led_res <= 1'b0;
        end else begin
            if (rx_done)      led_rx  <= 1'b1;  // LD0: any byte received
            if (packet_error) led_err <= 1'b1;  // LD1: checksum NACK fired
            if (result_valid) led_res <= 1'b1;  // LD2: MLP produced a result
        end
    end
    assign led = {led_res, led_err, led_rx};
```

LD2 is your direct probe for finding #1: after the MLP fix, LD2 must light on a valid packet.
With the current IP it never will.

---

## 3. BLOCKING — Synthesis reports are stale and were measured on dead logic

**Evidence:**
- `mlp/phase5_fpga/scripts/utilization_report.txt` / `timing_report.txt`: dated **2026-05-27**.
- Commit `a842c7e` ("Fix multi-driven reg bug in mlp_controller — features_q0 was always zero"):
  **2026-05-29**.

The reported 9,588 LUT (46%) and WNS +0.111 ns describe a netlist in which Vivado resolved the
`features_q0` multi-driver conflict by keeping the GND driver — constant-zero MLP input — and
swept large cones of downstream logic. PHASE5_COMPLETE's "Final Results" table,
`artifacts/mlp/synthesis_report.json`, and the green `tests/test_phase5.py` results all inherit this
invalid measurement. The HLS estimate for the MLP alone is 16,234 LUT; UART + controller add more.
**The fixed design may exceed 20,800 LUT or fail timing.**

**Fix:**
1. After restoring `top.v` (and again after the Phase 4 regeneration), re-run
   `create_project.tcl` + `run_synth.tcl`, regenerate both reports, re-run
   `parse_reports.py`.
2. Correct PHASE5_COMPLETE.md or add an addendum noting the original numbers were invalid.
3. **Process guard:** add a check that fails loudly on multi-driven nets — in `run_synth.tcl`,
   after synthesis:
   ```tcl
   set md [get_msg_config -severity {CRITICAL WARNING} -count]
   # and/or: report_drc; grep the log for "multi-driven" / "MDRV"
   ```
   At minimum, grep the synthesis log for `multi-driven` and abort if found. This bug class
   (legal-looking Verilog, silently broken netlist) is exactly what slipped through.

---

## 4. Resolved — the UART pin question

The official Digilent Basys 3 master XDC:

```
## USB-RS232 Interface
set_property PACKAGE_PIN B18 [get_ports RsRx]   ;# FPGA receives on B18
set_property PACKAGE_PIN A18 [get_ports RsTx]   ;# FPGA transmits on A18
```

The current `basys3.xdc` (B18=`uart_rxd`, A18=`uart_txd`) is **correct**. The debugging history
is fully explained without a pin bug:

| Attempt | What actually happened |
|---|---|
| B18=rxd, smoke test → timeout | Pins fine; MLP deadlocked (finding #1). LD0 didn't exist in that build, so "no RX evidence" was an artifact. |
| A18=rxd → 0 bytes, LD0 dark | Expected: A18 is the FPGA's TX pin; nothing ever arrives on it. The swap *created* the dead-RX symptom. |
| B18=rxd loopback | Not yet run — **expect PASS**. |

**Action:** run the loopback test as planned to close the question empirically, then never touch
these two constraints again (add a comment in the XDC citing the Digilent master XDC).

---

## 5. SHOULD FIX — HDL robustness (silent-hang failure modes)

These don't block the loopback/NACK milestones but each one converts a recoverable glitch into a
"board is hung, press BTNC" incident.

### 5.1 `uart_framing.v` — no mid-packet timeout (`:64-72`)
Once in `RX_RECV_FEATURES`, a single dropped byte desyncs the FSM **permanently**: it consumes
the next packet's `0xAA` as feature data. The SOF byte only resyncs from `WAIT_SOF` — the
PHASE5 doc's claim that SOF allows resync after dropped bytes is wrong mid-packet.

```verilog
// Add to RX FSM: ~10 ms inter-byte watchdog (1,000,000 cycles @ 100 MHz)
reg [19:0] rx_timeout_cnt;
always @(posedge clk) begin
    if (rst || rx_done)                rx_timeout_cnt <= 20'd0;
    else if (rx_state != RX_WAIT_SOF)  rx_timeout_cnt <= rx_timeout_cnt + 1;
    else                               rx_timeout_cnt <= 20'd0;
end
wire rx_timeout = (rx_timeout_cnt == 20'd1_000_000);
// in the FSM: if (rx_timeout) rx_state <= RX_WAIT_SOF;
```

### 5.2 `mlp_controller.v` — no `WAIT_DONE` watchdog (`:116-121`)
If the MLP never finishes (exactly today's bug), the controller hangs forever and gives the host
zero feedback. Add a timeout (expected latency ≈ 1,760 cycles; 1 ms = 100,000 cycles is generous)
that returns to IDLE and reports a distinct status:

- Extend the protocol: `status 0x02 = inference timeout`. Plumb a `result_timeout` flag to
  `uart_framing` and send `0x55 00 00 02`. Update `fpga_client.py` to surface it
  (`STATUS_TIMEOUT = 0x02`). This one byte of protocol would have turned a multi-day pin-swap
  hunt into "the board says the MLP never finished".

### 5.3 `mlp_controller.v` — win byte wraps at sigmoid = 1.0 (`:124`)
`result_win = layer9_out[11:4]` with no saturation. If the sigmoid LUT emits exactly 1.0
(`0x01000` in ap_fixed<18,6>), bits [11:4] = `0x00` → a 100% prediction reads as **0%**.

```verilog
// fixed<18,6>: [17]=sign, [16:12]=integer, [11:0]=fraction
result_win <= (win_result[17])        ? 8'h00 :          // negative → clamp 0
              (|win_result[16:12])    ? 8'hFF :          // >= 1.0  → clamp 255
                                        win_result[11:4];
```

Also note: `result_spread = layer10_out[23:16]` **floors** (−3.2 → −4) while the Python golden
values **round**. Absorbed by the ±3 tolerance, but add a comment so nobody "fixes" the
off-by-one later.

### 5.4 Dropped packets while busy
`IDLE` requires `packet_valid && ap_idle` (`mlp_controller.v:102`). A packet arriving while the
MLP is busy is silently discarded — the host just times out. Lowest-effort fix: also send the
NACK/busy response when `packet_valid && !ap_idle`. (At 2.4 ms round-trip vs 2 s host timeout
this is rare, but it's free diagnosability.)

---

## 6. SHOULD FIX — Phase 6 verification gaps

### 6.1 `ACTUAL_PORT_WIDTHS.txt` is missing
Listed as a deliverable in PHASE6_COMPLETE.md, required by
`tests/test_phase6.py::test_actual_port_widths_file_exists` (`:109-112`) — **that test fails
today**, so the "28/28" and "105 passed" claims are not currently reproducible. Regenerate it
from `artifacts/mlp/ip_repo/hdl/verilog/myproject.v` (audit-verified widths: `features_q0` 18-bit,
`layer9_out` 18-bit, `layer10_out` 32-bit, `features_address0` 5-bit). Commit it this time.

### 6.2 Golden expected outputs ignore input quantization
`run_sim.py:32-34` computes expected predictions from `X_float`, but the FPGA computes from
`round(x*255)/256` (byte placed in fractional bits [11:4], a systematic ×255/256 ≈ −0.4% scale
plus rounding). The real-Verilog sim already measured game 0 at win-delta **12 of 13 allowed** —
one count of margin. On the board, the same tolerance must absorb nothing extra, but the margin
is uncomfortably thin and any future re-quantization could flip it.

**Fix:** in `load_test_vectors()`, compute expected outputs from the quantized inputs:
```python
feat_uint8 = np.clip(np.round(X_float * 255), 0, 255)
X_hw = feat_uint8.astype('float32') / 256.0     # exactly what the FPGA sees
preds = model.predict(X_hw, verbose=0)
```
This makes the golden test isolate *hardware arithmetic* error instead of bundling input
quantization into the tolerance.

### 6.3 Stub-based regression validates plumbing only
By design (outputs are injected into the stub), the 50-game regression proves the UART pipeline
round-trips bytes — it can never catch an MLP-side bug. Fine, but PHASE6_COMPLETE should say so
explicitly, and after the Phase 4 fix the real-Verilog test (no FIFO stubs) becomes the load-
bearing arithmetic check. Consider raising it from 3 games to all 50 once it runs un-stubbed.

### 6.4 Doc contradiction about the `!tx_start` guard
PHASE6_COMPLETE "Error 1" says the guard was **removed**; commit `225a5eb` and PHASE7_STATUS say
it was **added** and is correct. The code has the guard, and the audit confirms it's required
(without it, the win byte's `tx_start` pulse fires while `uart_tx` is mid-latch and the byte is
swallowed — host receives 3 of 4 bytes). Fix the Phase 6 doc.

### 6.5 `game_metadata.csv` duplicate `week` column
`run_sim.py:81-82` — `week` is both a metadata column and a feature name, so the header has two
`week` columns and the metadata value renders as `1.0000`. Rename the metadata one to
`game_week`. Cosmetic.

---

## 7. SHOULD FIX — Phase 7 deployment code

### 7.1 `FPGAClient.reset_board()` doesn't reset anything and can desync framing
(`fpga_client.py:123-132`) — a UART break holds the line low; `uart_rx` decodes that as `0x00`
byte(s). If the framing FSM is mid-packet, the break *advances* it with garbage. The FPGA has no
reset path from UART; only BTNC resets it. **Fix:** delete the method or rename it
`flush_host_buffers()` with a docstring stating it cannot reset the FPGA. (After §5.1's timeout
exists, a deliberate >10 ms silence actually becomes a soft resync — but only after that fix.)

### 7.2 Decode tests are tautologies
`tests/test_phase7.py:62-70` assert literals like `128/256.0 == 0.5` — they pass even if
`FPGAClient`'s parsing breaks. Replace with a test that feeds a canned 4-byte response through
the client (mock `serial.Serial.read`) and checks `win_prob`/`spread`/`status`.

### 7.3 `from_current_week()` uses stale context features
(`feature_builder.py:133-139`) — for an upcoming game it reuses the home team's *previous game*
`vegas_spread`, `vegas_total`, `temp`, `wind`, and each team's prior `rest_days`. `vegas_spread`
is the strongest single feature, and a stale line from a different matchup can dominate the
prediction. Partially commented in code, but the UI (`app.py`) should display a visible
"approximate features" warning for this path, and the docstring should quantify the caveat.

### 7.4 Feature count hardcoded
`fpga_client.py:68` (`!= 21`), `run_sim.py` (`range(21)`), several tests. Derive from
`len(features.json)` where Python-side. (In HDL the 21/168-bit widths are inherently synthesis-
fixed — that's correct and already commented.)

---

## 8. MINOR — docs, hygiene, consistency

| Item | Where | Fix |
|---|---|---|
| Spread-MAE gate inconsistency: CLAUDE.md says ≤ 9.0; PHASE2_COMPLETE relaxed it to 10.5 (val MAE 9.74 beats Vegas 9.76); **no pytest enforces any absolute MAE** | `CLAUDE.md`, `tests/test_phase2.py` | Update CLAUDE.md to 10.5 with the Vegas-baseline rationale; add a val-MAE ≤ 10.5 test |
| Win decode documented as "divide by 255" but everything implements /256 | `PHASE5_COMPLETE.md:31` | Fix the doc (code is consistent: hw `[11:4]` ≈ p×256, client `/256.0`, vectors `round(p*256)`) |
| Duplicate MLP stubs that can drift | `mlp/phase5_fpga/hdl/myproject_stub.v`, `myproject_stub_fast.v` vs `mlp/phase6_sim/stubs/myproject_stub.v` | Keep only the phase6 one; the phase5 copies were for iverilog syntax checks |
| Temp debug scripts flagged for deletion in PHASE7_STATUS | `test_win.py`, `test_uart_raw.py` (repo root) | Delete before final commit (after they've served the loopback/NACK steps) |
| `rst` (BTNC) used directly as synchronous reset without a 2-FF synchronizer | `top.v` | Low risk (sync-reset design, human-speed button), but double-flopping `rst` is one line of insurance |
| `uart_tx.tx` powers up 0 for ~1 cycle at configuration (registers init to 0, IDLE drives 1 next cycle) | `uart_tx.v` | Harmless; host may see one garbage byte right after programming — `connect()` already flushes input. Optionally `reg tx = 1'b1;` initial value |
| Comment says "Run up to 200000 cycles", loop runs 30000 | `test_mlp_verilog.py:74-76` | Align comment/loop |

---

## 9. Verified clean (audited, no findings)

End-to-end chain — every step checked against every other step:

| Link | Verdict |
|---|---|
| `features.json` order (21, locked) ↔ `train.py` ↔ `convert.py` ↔ `run_sim.py` ↔ `feature_builder.py` | consistent; byte 0 = feature 0 everywhere |
| Encoding `uint8 = clip(round(scaler·255))` | identical in `feature_builder.py:104/173`, `run_sim.py:44-46`, `uart_helpers.py:83` |
| Packet `0xAA + 21 + XOR(features)` / response `0x55 + win + spread + status` | identical in `fpga_client.py`, `uart_helpers.py`, `uart_framing.v`, debug scripts |
| `uart_framing` byte_cnt → `feature_bus[i*8+:8]` → `feature_store[i]` → MLP `features_address0` | index-consistent; matches features.json order assumed by HLS weights |
| `features_q0 = {6'b0, byte, 4'b0}` (byte/256, −0.4% systematic) | consistent HDL ↔ sim ↔ docs; absorbed in tolerance (see §6.2 for margin note) |
| Output decode win=`[11:4]`, spread=`[23:16]` | consistent across `mlp_controller.v`, `uart_helpers.py`, `test_mlp_verilog.py`, docs (see §5.3 for saturation edge case) |
| IP port widths (q0=18, layer9=18, layer10=32, addr=5) | verified directly in `myproject.v:47-59`; `top.v` (committed) wiring matches exactly |
| `scaler.pkl` never refit outside Phase-2 creation | `train.py:86` fits once; `eval_repeated.py` refits only a local throwaway, never saves |
| `model_quantized.keras` used for all golden vectors / inference comparisons | `run_sim.py:30`, `resource_report.py:58`; `model_best.keras` only in Phase-2 sanity + Phase-3 transfer (correct) |
| Temporal splits (≤2020 / 21–22 / 23–24), `.shift(1)` on all rolling stats, pre-game Elo | verified in `train.py`, `features.py`, `epa.py`, `qb.py`, `convert.py:229`, `run_sim.py:25` |
| `uart_rx.v` / `uart_tx.v` bit timing (868 clks, mid-bit sampling, double-flopped RX, full reset coverage, power-up state = IDLE without BTNC) | correct |
| `!tx_busy && !tx_start` guards in TX FSM | correct and required (see §6.4) |
| `features_q0` single-driver fix (`a842c7e`) | correct as committed |
| cocotb unit-test counts (7/5/5/5), vector checksums, 50-row counts | match docs; `test_checksums_correct` genuinely recomputes XOR |

---

## 10. Recommended order of operations

1. **Loopback test** (already staged). Expect PASS with current XDC → pin question closed forever.
2. **Restore full `top.v`** from `HEAD` + add the 3-LED port (§2). Re-synthesize.
3. **Regenerate synthesis reports** (§3). Check LUT/WNS for real. Grep log for multi-driven nets.
4. **Board: NACK test** (`test_win.py`) → expect `55 00 00 01`. This validates RX path, framing,
   checksum, TX path — everything except the MLP.
5. **Board: smoke test** → expect **hang** (LD0 on, LD2 off). That is finding #1 confirmed on
   silicon, not a UART bug.
6. **Fix Phase 4** (§1): io_stream (or patched templates), **cosim gate**, re-export IP, rewrite
   `mlp_controller.v` for the new interface if io_stream, re-synthesize, re-check budget/timing.
7. **Re-run Phase 6 honestly**: `test_mlp_verilog` with FIFO substitutions removed; regenerate
   golden vectors with quantized inputs (§6.2); restore `ACTUAL_PORT_WIDTHS.txt` (§6.1).
8. **Board again**: NACK → smoke → `golden_vector_test.py` → `FPGA_PORT=COM8 pytest tests/test_phase7.py`.
9. Apply robustness fixes (§5) and hygiene items (§7–8) — ideally before step 8 so the watchdog
   status byte is available during bring-up.

---

## 11. Problems you are likely to hit next — and how to handle them

Pre-mortem for the fix campaign above.

### 11.1 Post-fix synthesis blows the LUT budget
**Likelihood: high.** The 9,588-LUT result was an artifact of dead logic (§3). HLS estimates
16,234 for the MLP alone + ~1–2k for UART/controller against a 20,800 budget — and io_stream
changes the picture again in either direction.
**If over budget:** in order of cheapness — (a) raise ReuseFactor on dense_1 (largest layer;
beware: Run 3 showed `rf_gt_nin` sparsemux growth — only move between *valid* RF values and
re-measure); (b) narrow the global precision further, e.g. `fixed<16,6>` — re-run the C-sim
delta check (mean < 0.05, max < 0.10) before accepting; (c) reduce dense_1 width 128→64 in the
*model* — that re-opens Phases 2–3 (retrain, requantize) but the 5-seed harness showed
128→64 costs only ~0.006 accuracy; you'd still clear the 63% gate with margin.

### 11.2 Post-fix timing fails at 100 MHz (negative WNS)
**Likelihood: moderate.** HLS pre-route Fmax was 94.6 MHz; the +0.111 ns closure was measured on
the gutted netlist. The flagged path is `sparsemux → select → phi` in dense_1's ReuseLoop.
**Fixes, in order:** (a) let Vivado retry with `Performance_ExplorePostRoutePhysOpt`; (b) add a
pipeline register on the flagged path (HLS config: `config_compile -pipeline_style frp`, or one
extra register stage in the template); (c) **drop the clock to 50 MHz** — the design has no
hard 100 MHz requirement (inference 17.6 µs → 35 µs, still 60× faster than the UART transfer).
If you do: change `create_clock -period` to 20.0 **and** the `CLK_FREQ` parameter everywhere
(`top.v` passes it to both UART modules — `CLKS_PER_BIT` becomes 434) **and**
`uart_helpers.py:CLKS_PER_BIT` for sims. Use an MMCM or just constrain W5 differently? No —
W5 is a fixed 100 MHz oscillator; you'd add a clock divider/MMCM in `top.v`. The MMCM route is
cleaner than hand-dividing (no derived-clock constraint headaches).

### 11.3 io_stream conversion fails or produces a different interface than expected
**Likelihood: high (interface change is certain).** Expect `features` to become a stream port
and possibly packed differently (hls4ml io_stream packs `n_in` elements into struct beats).
**Plan:** after `convert.py`, *read the generated `myproject.v` port list first* (this project
has been burned twice by assuming port names), write the new `ACTUAL_PORT_WIDTHS.txt`, then
write the new controller against it. Re-check: input precision may no longer be `fixed<18,6>`
per-element — confirm where the byte lands in the new representation before reusing the
`{6'b0, byte, 4'b0}` trick.

### 11.4 Cosim itself hangs or errors
**Likelihood: moderate** (cosim is exactly the test that catches stream bugs — that's why it's
the gate). A hang means the regenerated RTL still has a rate mismatch: re-check FIFO depths
(`config_dataflow -default_fifo_depth`), and that no stream has two consumers. Cosim needs the
C testbench from `convert.py`'s csim setup — keep the same input vectors so csim/cosim/Verilog
deltas are comparable. Run it on the Windows side via the existing `run_synthesis.bat` flow
(Vitis HLS isn't installed in WSL).

### 11.5 Golden vector game 0 fails by 1–2 counts on the board
**Likelihood: moderate** until §6.2 is applied (current margin: 1 count on game 0).
**Fix:** regenerate `expected_outputs.csv` from quantized inputs first (§6.2). If a game still
fails after that, the residual is genuine fixed-point drift: either accept ±15 counts with a
written justification, or tighten the hardware (more fractional bits) — don't silently widen
tolerances per-game.

### 11.6 Board bring-up environment gotchas (recurring from PHASE7 history)
- **Bitstream is volatile** — every power cycle reloads the Digilent demo (7-seg counting =
  unprogrammed). Reprogram via `program_board.py` each session; seeing the demo means "not
  programmed", not "broken".
- **COM port number can move** (COM8 today). If `connect()` fails, check Device Manager; the
  FT2232's *channel B* is the UART.
- **Port contention:** only one process can hold COM8 — close PuTTY/screen/another Python before
  pytest. Vivado Hardware Manager (JTAG, channel A) can stay open; it does not lock channel B.
- **First byte after programming may be garbage** (`tx` powers up low for a cycle);
  `connect()` already flushes — also flush after every reprogram, before the first test.
- **WSL2 vs Windows:** keep all pyserial board tests on Windows Python/COM8 as PHASE7 already
  recommends; usbipd adds failure modes for zero benefit.
- **Git from Windows over `\\wsl.localhost`** hits "dubious ownership" — run git inside WSL
  (`wsl -e git -C /home/younix/nfl-fpga-accelerator …`) rather than adding a global exception.

### 11.7 After everything passes: regression-proofing
- Make `cosim_design` and "test_mlp_verilog with zero FIFO substitutions" permanent CI steps for
  any future Phase 4 re-run.
- Add a pytest that asserts `utilization_report.txt` is newer than every file in
  `mlp/phase5_fpga/hdl/` + `artifacts/mlp/ip_repo/` — stale-report bugs (§3) become impossible.
- Add the multi-driven-net log grep to `run_synth.tcl` (§3).

---

*Audit performed file-by-file; no changes were made to any source file. All line numbers refer
to the working tree as of 2026-06-11.*
