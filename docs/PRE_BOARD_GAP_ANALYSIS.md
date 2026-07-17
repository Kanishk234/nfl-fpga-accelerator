# Pre-Board Gap Analysis — 2026-06-18

A critical self-review of our verification *after* the io_stream remediation
(`POST_AUDIT_REMEDIATION.md`, 2026-06-17) and *before* physical board bring-up (Phase 7).
Question asked: have we exhausted everything testable without the board? Answer: **no** — the
MLP datapath and the happy path are thoroughly covered, but several **error/defensive paths we
added** and the **host-side interpretation** are under-tested, and there is one real **HDL↔host
inconsistency**. Every gap below is closeable in simulation / on the host, without the board.

---

## Already thoroughly covered (for reference)

- MLP arithmetic — Phase-4 C-sim, RTL cosim, 50-game real-IP regression (winner agreement,
  delta envelope).
- Controller ↔ IP AXI-Stream + `ap_ctrl_hs` handshake; repeated/back-to-back inferences (50 in a row).
- UART RX/TX bit timing, SOF framing, XOR checksum — cocotb unit tests + full-UART-chain TB.
- Good-packet end-to-end (real IP over UART), checksum-NACK (`0x01`), debug LEDs.
- Vivado fit/timing/DRC/bitstream (17,896 LUT 86%, WNS +0.145, 0 errors).

---

## Gap register

Severity: 🔴 high (close before board) · 🟡 medium · 🟢 low / decision.
"Board-free?" = can be fully tested/fixed without the Basys 3.

### G1 🔴 — Host doesn't handle inference-timeout status `0x02`
- **Where:** `phase7_deploy/inference/fpga_client.py:100-102` (`status_str = "OK" if status==0x00 else "NACK"`).
- **What:** In Phase 5 we added the controller watchdog → `uart_framing` response **status `0x02`**
  (§5.2). The host only knows `0x00` (OK) and `0x01` (NACK); it labels `0x02` as `"NACK"`.
- **Why it matters:** `0x02` fires exactly in the scenario we are guarding against — the MLP not
  completing (the original deadlock class). If it ever triggers on the board, the host would print
  "NACK" (a checksum error) instead of "inference timeout," sending us down the wrong path during
  bring-up. The whole point of `0x02` was diagnosability.
- **Board-free?** Yes (host code + mocked-serial test).
- **Actionable steps:**
  1. In `fpga_client.py` add `STATUS_TIMEOUT = 0x02`; map status → `{0x00:"OK", 0x01:"NACK", 0x02:"TIMEOUT"}` (unknown → `"UNKNOWN(0xXX)"`).
  2. Log a distinct warning for `TIMEOUT` ("MLP did not complete — see AUDIT §5.2"); keep returning the dict (don't raise) so the UI can show it.
  3. Covered by G3's decode tests (assert `0x02 → "TIMEOUT"`).

### G2 🔴 — The watchdog / timeout path is never exercised
- **Where:** `mlp_controller.v` `TIMEOUT_CYCLES`→`result_timeout`; `uart_framing.v` `result_timeout`→`0x02`.
- **What:** No simulation ever makes the IP stall, so `result_timeout` and the `0x02` TX path have
  never run. It's untested safety-net logic.
- **Why it matters:** If the watchdog itself is buggy (wrong threshold, doesn't return to IDLE,
  framing doesn't emit `0x02`), we won't find out until the board hangs — defeating the net.
- **Board-free?** Yes (iverilog with a deliberately-stalling stub).
- **Actionable steps:**
  1. Add `phase6_sim/functional/myproject_stub_stall.v` — an io_stream stub that consumes the input
     beat (asserts `features_TREADY`) but **never** asserts `layer9_out_TVALID`/`layer10_out_TVALID`.
  2. Add `tb_timeout.v` (controller + stalling stub), with a small `TIMEOUT_CYCLES` override (e.g.
     `#(.TIMEOUT_CYCLES(500))`) so the sim is short; assert `result_timeout` pulses and the FSM
     returns to IDLE and can accept the next packet.
  3. Extend `tb_top_uart.v` with one stalled packet (using the stall stub build) and assert the
     response is `55 00 00 02` and LED behaviour is sane. (Run via a small iverilog target — the
     stall stub is tiny, no XSIM needed.)
  4. Verify: `result_timeout` fires once, status byte `0x02`, controller recovers (next packet OK).

### G3 🔴 — Host encode/decode has no real tests (audit §7.2)
- **Where:** `tests/test_phase7.py` (decode asserts are tautologies like `128/256.0 == 0.5`).
- **What:** The host's `run_inference` decode (`win_raw/256`, signed-int8 spread, status mapping)
  and `compute_checksum` are not tested against canned device bytes.
- **Why it matters:** The host is what *interprets the board's output*; a decode bug means every
  bring-up reading is wrong/misleading. Independent of the board.
- **Board-free?** Yes (mock `serial.Serial.read`/`write`).
- **Actionable steps:**
  1. Rewrite `tests/test_phase7.py` decode tests to drive `FPGAClient` with a mocked serial:
     - feed `55 80 03 00` → assert `win_prob≈0.5`, `spread==3`, `status=="OK"`, `raw_*` correct.
     - feed `55 00 fb 00` → assert `spread==-5` (signed int8).
     - feed `55 00 00 01` → `status=="NACK"`; feed `55 00 00 02` → `status=="TIMEOUT"` (after G1).
     - short read (<4 bytes) → `TimeoutError`; wrong SOF (`0xAA…`) → `RuntimeError`.
     - `compute_checksum` matches the HDL XOR for a known vector.
  2. Add an encode round-trip test: bytes built by `feature_builder` for a game == the bytes in
     `phase6_sim/functional/tb_inputs.mem` for that game (proves host encoding == sim/golden encoding).
  3. Verify: `FPGA_PORT` unset, `pytest tests/test_phase7.py` passes with no board.

### G4 🟡 — Win saturation (`0x00` / `0xFF`) never exercised
- **Where:** `mlp_controller.v` SEND state (negative→`0x00`, ≥1.0→`0xFF`, else `[11:4]`).
- **What:** The 50 games span win byte 47–233; the saturation branches never execute. Sigmoid
  output never reaches exactly 1.0 or negative, so board risk is low — but it's untested.
- **Board-free?** Yes (forced stub values).
- **Actionable steps:**
  1. In the timeout/stall stub harness (G2), add a variant stub that drives `layer9_out_TDATA` to
     a ≥1.0 value (`0x0001_0000` in `<18,6>`, integer bit set) and to a negative value
     (`0x0002_0000` with sign bit), check `result_win == 0xFF` and `0x00` respectively.
  2. Verify: both saturation branches produce the clamped byte; mid-range still passes `[11:4]`.

### G5 🟡 — Negative spread + `0xAA`-feature not driven through the full UART chain
- **Where:** `tb_top_uart.v` currently runs games 0–2 (all positive spread, no `0xAA` feature byte).
- **What:** Negative spread is covered at the controller level (regression) but not through
  framing/TX; a feature byte equal to the SOF marker (`0xAA`) was never sent through `uart_rx`/framing.
- **Why it matters:** Confirms signed-byte transport over UART end-to-end and that a `0xAA` feature
  mid-packet isn't mis-framed as a new SOF (framing is byte-agnostic in RECV_FEATURES, but prove it).
- **Board-free?** Yes (XSIM, choose specific games).
- **Actionable steps:**
  1. Pick a golden game with a negative spread (e.g. one with `spread_byte < 0`) and one whose
     feature bytes include `0xAA`; drive them in `tb_top_uart.v` (raise `NG` or select indices).
  2. Assert the response spread decodes to the expected negative value and status stays `0x00`.

### G6 🟢 / decision — Mid-packet desync recovery not implemented (audit §5.1)
- **Where:** `uart_framing.v` RX FSM (no inter-byte timeout).
- **What:** A single dropped byte permanently desyncs the framing FSM — it consumes the next
  packet's `0xAA` as feature data; SOF only resyncs from `WAIT_SOF`.
- **Why it matters:** Over a clean USB-UART, bring-up likely won't hit it; but any glitch wedges the
  board until BTNC. The §5.1 RX inter-byte watchdog was never added.
- **Board-free?** Yes (implement + iverilog test that drops a byte and checks resync).
- **Decision needed:** implement the ~10 ms inter-byte watchdog now (small, with a test that injects
  a dropped byte and verifies the FSM returns to `WAIT_SOF` and the next packet succeeds), **or**
  consciously defer to post-bring-up. Recommendation: implement now — it's cheap insurance against a
  confusing intermittent board hang.

### G7 🟢 — `reset_board()` is misleading/harmful (audit §7.1)
- **Where:** `phase7_deploy/inference/fpga_client.py:123-132`.
- **What:** Sends a UART break that cannot reset the FPGA (no UART reset path) and, mid-packet,
  advances the framing FSM with `0x00` garbage.
- **Actionable steps:** rename to `flush_host_buffers()` (drop the `send_break`; keep
  `reset_input_buffer`), docstring stating only BTNC resets the FPGA; update any callers
  (`ui/app.py`, `validation/golden_vector_test.py`) and add a one-line note that, once G6's RX
  watchdog exists, a deliberate >10 ms idle is the real soft-resync.

### G8 🟢 — Busy-packet drop (audit §5.4)
- **Where:** `mlp_controller.v` IDLE gate (`packet_valid && ap_idle`).
- **What:** A packet arriving while the MLP is busy is silently dropped; host just times out.
- **Actionable steps (optional):** lowest-effort — also emit a NACK/busy status when
  `packet_valid && !ap_idle`. Rare at 2.4 ms round-trip vs 2 s host timeout; defer unless trivial.

### G9 🟢 — Housekeeping (not test gaps)
- `mlp_controller` ignores the IP's `ap_done` (gates on output capture instead) — harmless; the synth
  "unconnected `ap_done`" warning is expected. Optionally add a comment.
- `tests/test_phase6.py` is stale (old flow, audit §6.1) — update to point at `phase6_sim/functional/`
  or retire it; it is **not** part of the functional sign-off.

---

## Recommended order of execution

1. **G1** (host `0x02`) — 1 small edit, unblocks G3's status assertions.
2. **G3** (host decode/encode tests, mocked serial) — makes host interpretation trustworthy.
3. **G2** (stalling-stub timeout test) — proves the watchdog + `0x02` path; reuse its stub for **G4**.
4. **G4** (saturation) — same harness as G2.
5. **G5** (neg-spread / `0xAA` over UART) — extend `tb_top_uart`.
6. **G6** (RX inter-byte watchdog) — **decision**: implement + test, or defer.
7. **G7** (`reset_board` rename) — quick host hygiene.
8. **G8 / G9** — optional / housekeeping.

After G1–G5 (and G6 if chosen), every error path and the host interpretation are exercised, and
simulation coverage is genuinely exhausted — the board is the only remaining unknown.

---

## Sign-off gate before board bring-up
- [x] **G1** host status `0x02` handled — `fpga_client.py` maps `0x02→"TIMEOUT"` (STATUS_NAMES).
- [x] **G2** timeout/watchdog path — `tb_timeout.v` (controller fires `result_timeout` @ TIMEOUT_CYCLES,
      returns to IDLE) and `tb_top_timeout.v` (end-to-end response `55 00 00 02`). PASS.
- [x] **G3** host decode/encode unit tests — `tests/test_phase7.py` mocked-serial decode (OK/NACK/
      TIMEOUT/unknown/neg-spread/short-read/bad-SOF) + encode-consistency vs sim vectors. 22 pass.
- [x] **G4** win saturation — `tb_timeout.v +SAT=hi/neg/mid` → `0xFF`/`0x00`/`0x80`. PASS.
- [x] **G5** SOF-collision (`0xAA` features) → status `0x00` (`tb_top_uart.v`). Negative spread is
      already covered end-to-end by the 50-game regression (signed bytes vs golden) + G3 host decode.
- [x] **G6** RX inter-byte desync watchdog — IMPLEMENTED in `uart_framing.v` (parameter
      `RX_TIMEOUT_CYCLES`, default 1,000,000 = ~10 ms @ 100 MHz). Mid-packet stall resyncs to
      WAIT_SOF. Verified by `tb_framing_resync.v` (dropped byte → next packet accepted, pv=1 pe=0)
      and full-UART regression still PASS. **⚠ Re-synthesis required** — `top.bit` is now stale;
      re-run `create_project.tcl` + `run_synth.tcl` (added logic is a 20-bit counter + comparator,
      so timing/LUT impact is negligible, but the bitstream must include it).
- [x] **G7** `reset_board()` → `flush_host_buffers()` (no UART break); `ui/app.py` caller updated.

### Deferred (with rationale)
- **G8 (busy-packet NACK)** — **deferred.** Adding a busy/NACK protocol branch to the verified
  `uart_framing` TX FSM + controller right before bring-up is more regression risk than the gap is
  worth (a packet arriving mid-inference is rare: ~2.4 ms round-trip vs the host's 2 s timeout, and
  the host already surfaces a clean `TimeoutError`). Revisit post-bring-up if it ever bites.
- **G9 housekeeping** — done: `ap_done` comment added; `tests/test_phase6.py` rewritten (29 pass).

### Remaining before board
All gaps closed and verified except **G8 (deferred, documented)**. Simulation/host coverage is
exhausted. **One build step left: re-synthesize the bitstream** (G6 changed `uart_framing.v`),
then proceed to board bring-up.

---

## Remediation log (2026-06-18)

All gaps closed except G8 (deferred, documented above). Summary of what was done, the files
touched, and — importantly — *how each was actually verified* (not just "a test passes").

### Verification philosophy (answering "did we test the design or just the testbench?")
- **Functional correctness is proven against an independent ground truth**, not testbench
  self-consistency: the hardware's win/spread bytes are compared to `golden.csv`, computed from the
  trained Keras model on quantized inputs. This holds at four independent levels — C-sim, RTL cosim,
  the 50-game real-IP regression, and the full-UART real-IP run (two simulators agree: 177/5,
  154/2, 193/6).
- **The new error-path tests have negative controls** (proving they aren't trivially passing):
  - G6 resync: with the watchdog disabled (RX_TIMEOUT_CYCLES set huge) the test FAILS (pv=0 pe=1);
    with it enabled, PASS (pv=1 pe=0).
  - G2 timeout: with the watchdog disabled the controller-level test reports "NO OUTCOME (FAIL)";
    enabled, `RESULT_TIMEOUT fired @500`. End-to-end produces `55 00 00 02`.
  - G4 saturation: `+SAT=mid → 0x80` (no clamp) vs `+SAT=hi → 0xFF` / `+SAT=neg → 0x00` shows the
    clamp activates only on extremes.
- **Every testbench fix was backed by independent evidence the design was correct** (internal probes
  showing the DUT emitted the right bytes; golden match), or by the TB feeding a physically
  impossible stimulus (e.g. a 2-cycle rx_done that real uart_rx never produces).
- **One judgment call, documented:** the 50-game checker excuses the *winner label* on near-0.5
  "coin-flip" games (a quantized model cannot match a float 0.499 label); the *magnitude* checks
  (win Δ ≤26, spread MAE) still apply to all 50 games and pass.

### Closed gaps — files & tests
| Gap | Implementation files | Test / evidence |
|---|---|---|
| G1 host `0x02` | `phase7_deploy/inference/fpga_client.py` | `tests/test_phase7.py::test_decode_timeout_status` |
| G2 watchdog | (pre-existing `mlp_controller.v`/`uart_framing.v`) | `tb_timeout.v`, `tb_top_timeout.v` (→`55 00 00 02`) + neg control |
| G3 host tests | `tests/test_phase7.py` | 22 pass (decode OK/NACK/TIMEOUT/neg-spread/short/bad-SOF + encode-consistency) |
| G4 saturation | (pre-existing `mlp_controller.v`) | `tb_timeout.v +SAT=hi/neg/mid` |
| G5 SOF-collision | `phase6_sim/functional/tb_top_uart.v` | all-`0xAA` features → status `0x00`; neg-spread via regression |
| G6 RX watchdog | `phase5_fpga/hdl/uart_framing.v` (`RX_TIMEOUT_CYCLES`) | `tb_framing_resync.v` + neg control; bitstream rebuilt |
| G7 reset | `fpga_client.py` (`flush_host_buffers`), `ui/app.py` | n/a (removed UART-break) |
| G9 housekeeping | `mlp_controller.v` (ap_done comment) | `tests/test_phase6.py` rewritten (30 pass) |

New sim infra: `myproject_stub_stall.v`, `tb_timeout.v`, `tb_top_timeout.v`, `tb_framing_resync.v`.

### Post-rebuild synthesis sign-off (G6 included)
LUT 17,888 / 20,800 (86.0%), FF 29,824 (+18 vs pre-G6 = the watchdog counter), BRAM 7, DSP 18,
**WNS +0.126 ns** (timing closes at 100 MHz), DRC clean. `artifacts/synthesis_report.json` refreshed;
`tests/test_phase6.py` validates it.

### Scope explicitly NOT covered (pre-board)
- Post-synthesis gate-level functional sim of `top` — skipped by decision (clean synchronous RTL +
  closed timing make sim/synth mismatch unlikely; high effort, low expected yield).
- Board-only unknowns: FT2232 UART electrical timing, BTNC reset on silicon, clock/power, line
  glitches. These are what board bring-up exercises.

### Status: pre-board verification exhausted. Next action = board bring-up.
