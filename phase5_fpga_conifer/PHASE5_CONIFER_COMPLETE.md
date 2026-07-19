# Phase 5 (conifer) — Two-Stage GBDT Wrapper + Vivado Integration

> **Status: IN PROGRESS** — wrapper HDL is ✅ written and ✅ XSIM-verified
> bit-exact (100/100 games); Vivado synthesis/implementation/bitstream and the
> UART-level sim remain. This doc is updated as the phase advances and
> finalizes when the phase does.

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

## Remaining Work

| Step | What | Gate |
|---|---|---|
| UART-level sim | `tb_top_uart`-style test of `uart_framing_conifer` + `top_gbdt` (the chain regression stops at the controller boundary) | correct packet/NACK/timeout behavior |
| Vivado impl | Windows: `create_project.tcl` → `run_synth.tcl` | timing met; fits (IPs alone were 11,869 LUT / 57% — wrapper overhead expected small) |
| Board deploy | `phase7_deploy_conifer/` pyserial harness (new 65-byte packet) | bit-exact vs golden on hardware |
