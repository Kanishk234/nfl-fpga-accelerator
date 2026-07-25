# Phase 5 (conifer) — Two-Stage GBDT Wrapper + Vivado Integration

> **Status: COMPLETE** — wrapper HDL ✅ written, ✅ XSIM chain regression
> bit-exact (100/100 games), ✅ full-UART sim PASS, ✅ Vivado implementation:
> timing met (WNS +0.965 ns), **12,095 LUTs (58%)**, bitstream built.
> Board deploy is phase 7 (`phase7_deploy_conifer/`).

This is the GBDT analog of `phase5_fpga/` (MLP UART integration). The MLP
originals are untouched — everything modified lives here, per the `_conifer`
sibling-folder convention. `uart_rx.v` / `uart_tx.v` are *sourced* from
`phase5_fpga/hdl/` unchanged (single source of truth, not copied).

## What Was Built

| File | Role |
|---|---|
| `hdl/gbdt_controller.v` | FSM chaining win IP → sigmoid ROM → spread IP → `+vegas` adder; 1 ms watchdog (mirrors mlp_controller's AUDIT §5.2) |
| `hdl/sigmoid_rom.v` | 1024×12 sync-read ROM, `$readmemh` from the committed `sigmoid_lut.mem` |
| `hdl/uart_framing_conifer.v` | New packet format for raw 24-bit features (below) |
| `hdl/top_gbdt.v` | Wires UART + framing + controller + ROM + **both** conifer IPs |
| `scripts/create_project.tcl` | Vivado project (`C:/nfl_gbdt_build`, top `top_gbdt`), source guards |
| `scripts/run_synth.tcl` | synth → multi-driven-net guard → impl → bitstream → reports |

## The Design

```
UART rx ──► framing ──► controller ──► conifer_win ──► margin
                            │                            │
                            │              sigmoid ROM ◄─┘  (1024×12, spec in
                            │                   │            make_chain_golden.py)
                            │                win_prob ──► conifer_spread.x_21
                            │                                │
                            └── features[0..20] ────────────►│
                                                          residual
                            spread = residual + x_16 (vegas_spread) ──► UART tx
```

- All values `ap_fixed<24,12>`. **22-cycle end-to-end latency** (9+9 per IP +
  sigmoid + handshakes) vs ~1,800 cycles for the MLP.
- The conifer IP interface is far simpler than the MLP's: `ap_ctrl_hs` +
  parallel 24-bit `x_i` ports + `score_0`/`score_0_ap_vld`. No AXIS, no
  streams, no deadlock class. Two quirks: **`ap_rst` is active-HIGH** (hls4ml's
  was `ap_rst_n`), and **`score_1` is a dead template input port** (never read
  internally in either IP) — tied to 0.

## UART Protocol (changed vs MLP — raw features don't fit a byte)

The GBDT uses raw, unscaled features (trees are scale-invariant; elo ~1500),
so the MLP's 1-byte-per-feature INT8 packet cannot carry them:

- **RX (65 bytes):** `0xAA` | 63 feature bytes | XOR checksum —
  feature *i* = bytes 3i..3i+2, LSB first, value = `round(raw * 4096)`
  (24-bit two's complement)
- **TX (8 bytes):** `0x55` | win_prob[3B, LSB first] | spread[3B] | status
  (`0x00` ok, `0x01` checksum NACK, `0x02` inference watchdog)

Full-width results come back deliberately: the board output is bit-exact
comparable against the golden — no quantize-to-byte tolerance like the MLP's.

## Verification So Far — XSIM Chain Regression: PASS

`phase6_sim_conifer/run_xsim_gbdt.bat` drives controller + ROM + **both real
synthesized netlists** over 100 val games; `check_results.py` gates on EXACT
24-bit equality vs `chain_golden/golden_chain.npy` (no tolerance band):

```
timeouts           : 0
win_prob bit-exact : 100/100
spread   bit-exact : 100/100        22 cycles/game
```

**Bug caught before hardware:** the golden was originally computed on raw
float features, but the C++ emulation truncates (AP_TRN) where the host
rounds — a 1-ulp input skew that would have broken board bit-exactness
"mysteriously." Fix: the golden pre-quantizes inputs to `round(x*4096)` — the
exact words the UART carries (`chain_golden/tb_inputs.mem`).

## Full-UART Sim — PASS

`phase6_sim_conifer/run_xsim_uart_gbdt.bat` (`tb_top_uart_gbdt.v`) drives the
**entire `top_gbdt`** over real 115200-baud UART in XSIM — serializes the
65-byte request bit-by-bit into `uart_rxd`, deserializes the 8-byte response
from `uart_txd`, and self-checks against `golden_fixed.mem`:

- 3 games: **bit-exact** win_prob + spread words, status `0x00`, LEDs correct
- Corrupted checksum → NACK status `0x01`
- SOF-collision (all 63 feature bytes = `0xAA`) → treated as data, status `0x00`

## Vivado Implementation — Timing Met, Bitstream Built

Full flow (`build_all.tcl` → synth → multi-driven-net guard → impl →
bitstream) on the real device, 100 MHz:

| | GBDT (this design) | MLP (`phase5_fpga`) |
|---|---|---|
| Slice LUTs | **12,095 (58.15%)** | 17,896 (86%) |
| Slice Registers | 18,956 (45.6%) | — |
| DSPs | **0** | uses DSPs |
| BRAM | 0.5 tile (sigmoid ROM) | uses BRAM |
| WNS | **+0.965 ns** | +0.145 ns |
| Inference latency | 22 cycles / 220 ns | ~1,800 cycles |

The full two-stage GBDT design with UART wrapper is smaller, faster, and has
6.7× more timing slack than the MLP. Wrapper overhead over the bare IPs
(11,869 LUTs) is just 226 LUTs.

Bitstream: `C:/nfl_gbdt_build/nfl_gbdt_accelerator.runs/impl_1/top_gbdt.bit`
(build dir is Windows-local, not in the repo; regenerate via
`scripts/build_all.tcl`). Reports: `scripts/utilization_report.txt`,
`scripts/timing_report.txt` (committed).

## Remaining Work

| Step | What | Gate |
|---|---|---|
| Board deploy | `phase7_deploy_conifer/` pyserial harness (new 65-byte packet, COM8) | bit-exact vs golden on hardware; honest UART-bound latency framing |
