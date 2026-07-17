# Post-Audit Remediation — 2026-06-17

Companion to `AUDIT_REPORT.md`. That report (dated 2026-06-11) found that Phase 7 board
bring-up was failing for three independent blocking reasons. This document records everything
done in the 2026-06-17 session to remediate them: the work per phase, every error hit and how
it was solved, the key decisions and why, and the final verification state.

---

## TL;DR — outcome

The root cause of the Phase 7 failure (the synthesized MLP IP **deadlocked in real hardware by
construction**) is **fixed and verified end-to-end in simulation**. The design now:

- Uses hls4ml **`io_stream`** (proper `DATAFLOW`, rate-matched FIFOs) instead of the deadlocking
  `io_serial` — **RTL cosim passes** (no deadlock).
- Has a **rewritten `mlp_controller` + `top`** for the new AXI-Stream interface, with the audit's
  robustness fixes folded in (win saturation, inference watchdog, debug LEDs, reset sync).
- **Fits and closes timing**: real Vivado impl **17,896 LUT (86%)**, **WNS +0.145 ns @ 100 MHz**,
  bitstream generated, 0 errors / 0 critical warnings.
- Passes an **honest functional regression** (real IP, 50 games) and a **full-UART-chain** test
  (real IP, with NACK) — two independent simulators agreeing on the hardware output.

Only **physical board bring-up (Phase 7)** remains; it needs the board.

---

## Starting point — the audit's three blockers

1. **The MLP IP deadlocked in hardware by construction** (`io_serial`): a strictly sequential
   top-level FSM with depth-2 FIFOs between layers whose producer/consumer never ran concurrently
   (first layer fills its FIFO → `ap_done` never fires → permanent stall); plus layers that
   re-read consumed streams (config4/6 did 512 reads of 128 writes) and a dual-consumer
   `layer7_out`. Phase 6 had masked all this by swapping in deep/replay FIFO **stubs** that were
   never in the bitstream; **cosim was never run**.
2. **`top.v` was a loopback stub** and the committed full design would collide with the LED pins
   added to the XDC.
3. **The committed synthesis numbers were invalid** — measured before the `features_q0` multi-driver
   fix, on a netlist where the MLP input was tied to GND.

(The suspected B18/A18 UART pin swap was **not** a bug — the XDC was already correct.)

---

## Work by phase

### Phase 4 — HLS regeneration (`io_stream`)
- Switched `phase4_hls/convert.py` to `io_type='io_stream'` (decision rationale below).
- Regenerated the project; **GCC C-sim passed** (mean Δ **0.0473**, max **0.0964** — unchanged,
  precision `fixed<18,6>`). Generated C++ confirms the fix: `#pragma HLS DATAFLOW` present and the
  dual-consumer `layer7_out` resolved via `nnet::clone_stream` into two copies (one per head).
- Added a **cosim gate** to `synth_only.tcl`: `csynth → cosim_design -rtl verilog → export IP`,
  aborting before IP export if cosim fails. Wrote `make_tb_data.py` to emit real cosim vectors.
- **Result:** csynth + **RTL cosim PASS** — "max depth reached by any hls::stream is 1" (every
  dataflow FIFO drains; no deadlock), RTL outputs match the C model. IP re-exported.
- **New interface** (verified from the synthesized `myproject.v`, *not* assumed):
  - `features_TDATA[671:0]` — 21 features × 32-bit lanes; each `ap_fixed<18,6>` in the low 18 bits.
  - `layer9_out_TDATA[31:0]` — win, value in `[17:0]`; `layer10_out_TDATA[31:0]` — spread `<32,16>`.
  - AXI-Stream `TVALID/TREADY` handshakes; block-level `ap_ctrl_hs`; **`ap_rst_n` active-LOW**.

### Phase 5 — HDL rewrite + synthesis
- **`mlp_controller.v` rewritten** for AXI-Stream + `ap_ctrl_hs`: packs 21 feature bytes into the
  672-bit beat (`lane = {20'b0, byte, 4'b0}`), pushes with `TVALID/TREADY`, captures the two output
  beats. Folded in **win saturation** (§5.3) and a **WAIT watchdog** (§5.2, `result_timeout`).
- **`uart_framing.v`**: added `result_timeout` → response status **`0x02`** (vs `0x00` result /
  `0x01` checksum-NACK).
- **`top.v` restored** (was the loopback stub) with the new AXIS wiring, `ap_rst_n = ~rst_sync`,
  a **2-FF reset synchronizer** (§8), and **3 sticky debug LEDs** (LD0=rx, LD1=err, LD2=result —
  §2; LD2 is the direct deadlock probe).
- **`myproject_stub.v`** replaced with an `io_stream` behavioral model (iverilog elaboration/smoke
  only; not in the Vivado project).
- **Re-extracted the new IP** into `artifacts/ip_repo/` (replacing the deadlocking io_serial IP),
  refreshed `artifacts/xilinx_com_hls_myproject_1_0.zip`.
- Added synth **guards** (§3): `create_project.tcl` aborts if `ip_repo` lacks `features_TDATA`
  (won't synth the old IP); `run_synth.tcl` aborts on multi-driven nets.
- **Result (Vivado 2025.2, xc7a35t):** synth + impl + bitstream, 0 errors / 0 critical warnings.
  LUT **17,896 / 20,800 (86%)**, FF 29,806 (71.6%), DSP 18/90, BRAM 7 tiles, slices 7,939/8,150
  (97.4% — spread-out packing, not capacity; routed with ~17% routing util). **WNS +0.145 ns,
  hold +0.017 — timing closes at 100 MHz.**

### Phase 6 — honest functional verification (`phase6_sim/functional/`)
- `gen_vectors.py` → 50-game `tb_inputs.mem` + `golden.csv` (golden from the snapped inference
  model on the **quantized** inputs `byte/256` — the §6.2 fix, isolating hardware error).
- `tb_regression_real.v` (XSIM) → `mlp_controller` + the **real** IP, 50 games.
- `tb_top_uart.v` (XSIM) → the entire `top` over serialized UART, games + corrupted-checksum NACK.
- `check_results.py` → compares to golden with a principled criterion.
- **Results — all PASS:**
  - 50-game regression: 0 timeouts, **confident-game winner agreement 40/40 = 100%**, win delta
    mean **11.6** / max **25** counts (matches the C-sim envelope), spread MAE **1.34** / max **3**,
    1494 cycles/inference. The only 2 winner flips are games the float model itself scores
    0.499 / 0.486 (excused coin-flips, not bugs).
  - Full UART chain + real IP: games 0–2 → `55 b1 05 00` / `55 9a 02 00` / `55 c1 06 00` (win/spread
    **exactly** matching the golden), NACK → `55 00 00 01`, LEDs correct.
- **Cleanup:** deleted the io_serial-era `stubs/`, the stubbed cocotb tests
  (`test_mlp_controller/integration/regression/mlp_verilog`), `test_vectors/`, `run_sim.py`,
  `print_report.py`; trimmed `cocotb/Makefile` to the UART unit tests. `functional/` is now the
  authoritative suite.

---

## Key decisions and rationale

| Decision | Why |
|---|---|
| **`io_stream`** over patching the deprecated `io_serial` templates | Maintained path; emits correct DATAFLOW + clone_stream by construction; controller rewrite is simpler than maintaining a template fork. Risk was LUTs (DATAFLOW), mitigated by the next row. |
| **Gate LUT fit on the real Vivado number, not the HLS estimate** | HLS estimated 28,869 LUT (138%) — would have triggered a needless redesign. Real impl is 17,896 (86%). The HLS estimate overcounts ~38% for this sparsemux-heavy design. |
| **XSIM, not iverilog, for real-IP simulation** | iverilog runs the real HLS netlist at ~5 min/game and is X-pessimistic (HLS RTL doesn't reset every datapath reg → `x` on bring-up) even though the same RTL passes XSIM cosim. XSIM is compiled (seconds) and is the IP's validated simulator. iverilog is still fine for small leaf modules / the stub. |
| **Excuse near-0.5 "coin-flip" games in the winner-agreement criterion** | A quantized accelerator cannot match a float model's *discrete* label on a game the float model scores 0.499; only a winner flip on a *confident* game indicates a real datapath bug. Substantive checks (deltas within envelope, 0 timeouts, confident-game agreement) all pass. |
| **Full cleanup of the stubbed cocotb verification** | The FIFO replay stubs are the exact thing that hid the original deadlock; keeping them risks repeating that mistake. |

---

## Errors encountered and how they were solved

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | `ModuleNotFoundError: tensorflow` when running convert.py | The Bash tool is Git Bash on Windows reaching WSL over UNC; `source venv/bin/activate` (Linux venv) is a no-op there → fell back to Windows Python | Run via real WSL: `wsl.exe -e bash -lc 'source venv/bin/activate && …'` |
| 2 | git "dubious ownership" on the `\\wsl.localhost` path | Repo accessed over UNC from Windows git | Run git inside WSL, or `-c safe.directory='*'` for read-only queries |
| 3 | **cosim FAILED: `file weights/w2.txt does not exist`** | The cosim C reference model loads weights via `load_weights_from_txt`, but `synth_only.tcl` didn't stage the weights dir | Added `add_files -tb firmware/weights` (matches hls4ml's own `build_prj.tcl`). Cosim then PASSED. Not an RTL bug. |
| 4 | `xvlog ... Can not find file: *.v` | cmd/`xvlog` don't expand the `*.v` glob on Windows | Generate an explicit file list and pass `xvlog -f xvlog_files.f` |
| 5 | iverilog real-IP regression: ~5 min/game and `x` outputs | Large HLS netlist (interpreted sim) + iverilog X-pessimism on un-reset HLS datapath regs | Moved the real-IP regression to XSIM (the IP's validated, compiled simulator) |
| 6a | Full-UART TB: response bytes garbled / `x` | TB's UART receiver, **not** the DUT (DUT internals showed correct `win=0x80`, and it put the right bytes on the wire) | Diagnosed by peeking `u_top.result_win` / `u_top.tx_start` |
| 6b | Receiver skipped the WIN byte (`0x80`) | Per-byte start re-detection: `0x80` = start + 7 zero data bits looks like one long low, so resync latched the *next* byte's start | Lock to the SOF edge once, sample 4 contiguous frames at fixed offsets |
| 6c | Receiver consistently 2 bits late (read `55 60 40 c0`) | The DUT's fast turnaround starts transmitting the response **before** the last input byte's stop bit finishes; a sequential send-then-receive armed the receiver too late and missed SOF | Arm the receiver **concurrently** (`fork`) with the sender |
| 6d | `0x55` read fine while other bytes were wrong | `0x55` is alternating (periodic-2) so it reads identically under a 2-bit sampling shift — it masked the offset bug | Test/verify with non-alternating bytes (`0x80`, `0x03`) |

Lesson threaded through #6: the **HDL was correct the entire time**; every full-UART failure was the
hand-rolled testbench. The real laptop-side receiver (pyserial/hardware UART) does not have these
monitor artifacts. The corrected TB is now strong enough to catch a real RTL bug if one existed.

---

## Final verification status (the test ladder)

| Layer | What it proves | Status |
|---|---|---|
| 1. Phase-4 C-sim (GCC vs Python) | MLP arithmetic | ✅ |
| 2. Phase-4 RTL cosim (XSIM, real RTL + real FIFOs) | IP works in RTL, **no deadlock** | ✅ |
| 3. Controller ↔ stub handshake (iverilog) | AXIS/`ap_ctrl_hs` sequencing | ✅ |
| 4. Real IP + real controller, **50 games** vs golden (XSIM) | controller packing/decode + MLP arithmetic | ✅ |
| 4b. Full UART chain + NACK (iverilog + stub) | UART framing/checksum/serialization/LEDs | ✅ |
| 4b'. Full UART chain + **real IP** (XSIM) | the complete board path | ✅ |
| 5. Vivado synth / impl / timing / DRC / bitstream | fits the fabric, closes timing | ✅ |
| 6. Board bring-up (loopback → NACK → smoke → golden) | real silicon + real UART | ⏳ needs board |

---

## Mapping to audit findings

| Audit § | Finding | Resolution |
|---|---|---|
| §1 | io_serial deadlock | Fixed via io_stream; cosim gate proves no deadlock |
| §2 | top.v stub + LED/XDC mismatch | Full top.v restored with `led` port + sticky LEDs |
| §3 | stale/invalid synthesis numbers; multi-driven nets | Re-synthesized (valid 17,896 LUT / WNS +0.145); multi-driven guard added to run_synth.tcl |
| §4 | UART pins B18/A18 | Confirmed correct (no change) |
| §5.2 | no WAIT_DONE watchdog | Added `TIMEOUT_CYCLES` → `result_timeout` → status `0x02` |
| §5.3 | win byte wraps at sigmoid=1.0 | Saturation in mlp_controller (negative→0x00, ≥1.0→0xFF) |
| §6.2 | golden ignored input quantization | `gen_vectors.py` computes golden from quantized `byte/256` |
| §6.3 | stub regression validates plumbing only | Replaced with real-IP `functional/` regression |
| §8 (reset sync) | rst not double-flopped | 2-FF synchronizer in top.v |
| §8 / cleanup | duplicate/obsolete stubs | Deleted stubs/ + stubbed cocotb tests |
| §11.1 | post-fix LUT overflow risk | Did not materialize — 86% real |
| §11.2 | post-fix timing risk (Fmax 94.6) | Did not materialize — WNS +0.145, closes at 100 MHz |

---

## What's left

- **Phase 7 board bring-up** (needs the Basys 3): reprogram `top.bit`, then loopback →
  NACK (`55 00 00 01`) → smoke/single inference (**LD2 must light** — the deadlock probe) →
  golden-vector comparison via `fpga_client.py`.
- **Robustness items not yet done** (optional, pre-board nice-to-haves): RX inter-byte watchdog
  (§5.1), Phase 7 `reset_board()` rename (§7.1), and updating/retiring the stale
  `tests/test_phase6.py` (§6.1).
