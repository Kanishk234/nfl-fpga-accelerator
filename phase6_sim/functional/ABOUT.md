# Phase 6 — Honest functional verification (real io_stream IP)

These tests run the **real** hls4ml `io_stream` IP (from `artifacts/ip_repo/`) together with the
**real** hand-written HDL and check outputs against a Python golden across real NFL games. They
were added when Phase 4 was re-synthesized with `io_type='io_stream'` (the io_serial design
deadlocked in hardware — see `AUDIT_REPORT.md` §1). The original cocotb regression in `../cocotb/`
drove a *stub* and computed its golden from un-quantized inputs, so it could never catch an
MLP-side or controller bug. This suite closes that gap.

## What's here

| File | Purpose |
|---|---|
| `gen_vectors.py` | Generate `tb_inputs.mem` (50 games' feature bytes) + `golden.csv` (expected win/spread). Golden is computed from `convert.load_inference_model` (the snapped-weight model the IP was built from) on the **quantized** inputs `byte/256` — so the comparison isolates hardware arithmetic error, not input quantization (audit §6.2). |
| `tb_regression_real.v` | Drives `mlp_controller` + the **real** `myproject` IP over N games; writes `sim_results.csv` (game_idx, win_byte, spread_byte, timeout). The core arithmetic + controller-integration test. |
| `tb_top_uart.v` | Drives the **entire** `top` over serialized UART (uart_rx → framing → controller → real IP → uart_tx), a few games + a corrupted-checksum NACK; self-checks the 4-byte response and the debug LEDs. Covers framing/checksum/serialization that the controller-level test skips. |
| `run_xsim.bat` | Windows: build + run `tb_regression_real` in Vivado XSIM (all 50 games), copy `sim_results.csv` back to WSL. |
| `run_xsim_uart.bat` | Windows: build + run `tb_top_uart` in XSIM (full chain, real IP). Self-checking. |
| `check_results.py` | WSL: compare `sim_results.csv` vs `golden.csv` — winner agreement, win-delta, spread-MAE, PASS/FAIL. |

## How to run

1. **Generate vectors** (WSL):
   ```bash
   source venv/bin/activate
   python phase6_sim/functional/gen_vectors.py
   ```
2. **Arithmetic regression — 50 games** (Windows, Vivado XSIM):
   ```
   & "\\wsl.localhost\Ubuntu\home\younix\nfl-fpga-accelerator\phase6_sim\functional\run_xsim.bat"
   ```
   then check in WSL:
   ```bash
   python phase6_sim/functional/check_results.py
   ```
3. **Full UART chain + NACK** (Windows, Vivado XSIM — self-checking):
   ```
   & "\\wsl.localhost\Ubuntu\home\younix\nfl-fpga-accelerator\phase6_sim\functional\run_xsim_uart.bat"
   ```
   Look for `FULL-UART TEST: PASS`.

## Why XSIM, not iverilog

iverilog *compiles* the real IP (`iverilog -g2012 -I artifacts/ip_repo/hdl/verilog ...`) but is the
wrong tool here: ~5 minutes per game on this large HLS netlist (257-input sparsemuxes, 128-wide
layers), and X-pessimistic — HLS RTL doesn't reset every datapath register, so iverilog yields `x`
outputs on bring-up even though the *same* RTL passes XSIM cosim with correct data. XSIM is compiled
(seconds) and is the simulator the IP was validated against. iverilog is still fine for the small
stub (`myproject_stub.v`) — used to validate `tb_top_uart.v`'s UART plumbing quickly.

## Pass criteria (`check_results.py`)

- 0 timeouts (no controller/IP deadlock).
- No winner flip on a **confident** game (model `|win_prob - 0.5| >= 0.05`). Games the float model
  itself scores near 0.5 are excused — a quantized accelerator cannot be expected to match the
  float model's discrete label there; only a flip on a confident game is a real bug.
- Win delta mean ≤ 13 / max ≤ 26 counts, spread max ≤ 3 (the `fixed<18,6>` C-sim envelope).

## Results (2026-06-17)

- **50-game arithmetic regression:** PASS — 0 timeouts, confident-winner agreement 40/40 = 100%,
  win delta mean 11.6 / max 25 (matches C-sim envelope), spread MAE 1.34 / max 3. Each inference
  = 1494 cycles. (2 excused coin-flips: games the model scores 0.499 / 0.486.)
- **Full-UART chain + real IP:** PASS — games 0–2 returned `55 b1 05 00` / `55 9a 02 00` /
  `55 c1 06 00` (win/spread exactly matching the golden), NACK returned `55 00 00 01`, LEDs correct.

## Notes for future maintainers

- The bytes are transmitted back-to-back (~10 bit-times each); the DUT begins replying *before* the
  last input byte's stop bit finishes, so `tb_top_uart` arms its receiver **concurrently** (fork)
  with the sender and locks to the SOF edge once, then samples 4 contiguous frames at fixed offsets.
  Per-byte start re-detection skips mostly-low bytes (e.g. `0x80`); `0x55` masks offset bugs
  (alternating → periodic-2). Debug by peeking DUT internals (`u_top.result_win`, `u_top.tx_start`).
- `run_*.bat` stage sources to `C:\Temp\...` because XSIM needs the ROM `.dat` files + `tb_inputs.mem`
  at the run CWD, and to avoid UNC-path issues.
