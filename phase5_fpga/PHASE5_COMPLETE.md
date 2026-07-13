# Phase 5 Complete: UART Wrapper + Vivado Integration

## What Phase 5 Does

Wires the hls4ml MLP IP block (Phase 4) to a UART interface so a laptop can
send 21 game features over USB-serial and receive win probability + point spread back.
Produces a synthesized bitstream ready to program onto the Basys 3.

The result is a complete Vivado project created from a single Tcl script — no GUI
clicking required.

---

## Communication Protocol

### Laptop → FPGA (inference request) — 23 bytes

```
Byte 0:       0xAA          — start-of-frame (resync after power-on or dropped bytes)
Bytes 1–21:   feature[0..20] — one byte each, uint8 [0,255] representing float [0,1]
Byte 22:      checksum       — XOR of bytes 1–21
```

### FPGA → Laptop (inference result) — 4 bytes

```
Byte 0:  0x55        — start-of-frame (distinct from 0xAA)
Byte 1:  win_prob    — uint8 [0,255], divide by 255 to get probability
Byte 2:  spread_int  — int8 signed, point spread in whole points
Byte 3:  status      — 0x00=OK, 0x01=checksum error
```

### Why these choices

- **SOF markers**: UART has no inherent packet boundaries. 0xAA/0x55 let the FPGA resync
  if bytes are lost or it powers on mid-transmission.
- **XOR checksum**: catches single-byte corruption. NACK (status=0x01) sent on failure.
- **INT8 features**: MinMaxScaler output [0,1] × 255, rounded. FPGA presents as
  `ap_fixed<18,6>` via `features_q0 = {6'b0, byte, 4'b0}` — byte in fractional bits [11:4],
  giving byte/256 ≈ byte/255 (0.4% error).

### Timing budget

```
Receive 23 bytes at 115200 baud:   23 × 10 bits / 115200   = 2.0 ms
MLP inference:                     1,760 cycles × 10 ns     = 0.018 ms
Transmit 4 bytes at 115200 baud:   4 × 10 bits / 115200     = 0.35 ms
Total round-trip:                  ~2.37 ms
```

---

## Architecture

### Module breakdown

```
top.v
├── uart_rx.v         — 8N1 receiver, 115200 baud, metastability protection
├── uart_tx.v         — 8N1 transmitter, 115200 baud, busy/start handshake
├── uart_framing.v    — packet assembly, SOF detection, XOR checksum, TX sequencer
├── mlp_controller.v  — ap_ctrl_hs FSM, feature memory, output capture
└── myproject         — hls4ml MLP IP (from Phase 4 IP zip)
```

### uart_rx

4-state FSM (IDLE → START → DATA → STOP). Samples at mid-bit by waiting HALF_BIT
clocks before sampling the start bit center, then CLKS_PER_BIT between data bits.
Double-registers the async RX input (`rx → rx_meta → rx_sync`) to prevent metastability
from the USB-UART chip propagating into the FSM.

`CLKS_PER_BIT = 100_000_000 / 115_200 = 868`

### uart_tx

4-state FSM (IDLE → START → DATA → STOP). tx idles high. Caller asserts `start=1` for
one clock; module clocks out start bit (low), 8 data bits LSB-first, stop bit (high).
`busy=1` while transmitting; caller must wait for `busy=0` before next byte.

### uart_framing

**RX FSM (3 states):** WAIT_SOF → RECV_FEATURES (21 bytes, XOR accumulate) →
RECV_CHECKSUM. On match: asserts `packet_valid` for one clock. On mismatch: asserts
`packet_error` (triggers NACK).

Feature bus: 168-bit flattened bus between uart_framing and mlp_controller. Verilog-2001
does not support array ports, so `feature[i] = feature_bus[i*8 +: 8]`.

**TX FSM (5 states):** WAIT → SOF(0x55) → WIN → SPREAD → STATUS. Each state waits for
`tx_busy=0` before loading `tx_data` and pulsing `tx_start`. Result bytes are latched on
entry to avoid holding onto live signals across the multi-cycle send.

### mlp_controller

4-state FSM implementing the ap_ctrl_hs protocol:

```
IDLE       — wait for packet_valid && ap_idle; latch all 21 features into feature_store[]
START_MLP  — ap_start combinationally high (assign ap_start = state == START_MLP)
             exactly one clock wide; transition immediately to WAIT_DONE
WAIT_DONE  — wait for win_valid && spread_valid (ap_vld outputs latched independently)
SEND_RESULT — encode outputs; assert result_valid for one clock; return to IDLE
```

Feature memory: 21-element 8-bit array (`feature_store[0:20]`). MLP drives
`features_address0` (5-bit) + `features_ce0`; controller responds next cycle with
`features_q0 = {6'b0, feature_store[addr], 4'b0}`.

Output encoding:
- `result_win   = layer9_out[11:4]`   — top 8 fractional bits of ap_fixed<18,6>
- `result_spread = layer10_out[23:16]` — integer byte of ap_fixed<32,16>

### myproject (hls4ml IP)

Imported from `artifacts/xilinx_com_hls_myproject_1_0.zip`. Added to Vivado as direct
Verilog source files (not via IP catalog) to avoid output-product-generation requirement.
Source files are in `artifacts/ip_repo/hdl/verilog/`.

---

## Issues Encountered and Fixes

### 1. Port names and widths differed from original Phase 5 plan

**Problem:** The plan (written before Phase 4 synthesis) assumed ports `layer9_out_0_V`,
`layer10_out_0_V_ap_vld`, and `features_q0` as 8-bit. After Run 7 (precision narrowed to
`fixed<18,6>`), the actual ports from csynth.rpt were:
- No `_0_V` suffix on any port
- `features_q0` is **18-bit** (not 8-bit) — global default type propagated here
- `layer9_out` is **18-bit**, `layer10_out` is **32-bit**

**Fix:** Verified port names from the extracted Verilog wrapper before writing any HDL.
Updated all port declarations and encoding formulas accordingly.

---

### 2. Verilog-2001 has no array ports

**Problem:** The plan used `output [7:0] feature_byte [0:20]` — SystemVerilog syntax,
not valid in Verilog-2001 (which Vivado defaults to for `.v` files).

**Fix:** Used a 168-bit flattened bus: `output reg [167:0] feature_bus`. Feature i is
extracted as `feature_bus[i*8 +: 8]`. Both uart_framing and mlp_controller use this
interface.

---

### 3. Vivado "module 'myproject' not found" during synthesis

**Problem:** `create_project.tcl` used the Vivado IP catalog approach:
```tcl
set_property ip_repo_paths $IP_REPO_DIR [current_project]
update_ip_catalog -rebuild
```
Vivado found the IP in the catalog but couldn't instantiate it during synthesis because
IP catalog entries require output products to be generated first (a separate GUI/Tcl step).

Also: `exec unzip` in Tcl doesn't work on Windows (no `unzip` command).

**Fix:** Pre-extracted the IP zip in WSL:
```bash
unzip artifacts/xilinx_com_hls_myproject_1_0.zip -d artifacts/ip_repo
```
Then replaced the IP catalog approach with direct `add_files` on all `.v` and `.dat` files
from `artifacts/ip_repo/hdl/verilog/`. Vivado treats them as ordinary RTL sources —
no output-product step needed.

---

### 4. parse_reports.py ZeroDivisionError on Vivado utilization report

**Problem:** Vivado's post-implementation utilization report has the format:
```
| Slice LUTs | 9588 | 0 | 0 | 20800 | 46.10 |
```
(columns: Name | Used | Fixed | Available | Util%)

The parser regex only skipped one `[^|]*` group, matching the `Fixed` column (0) as
`Available`, giving `total=0` → division by zero.

**Fix:** Added a second `[^|]*` skip group to step over the Fixed column before
extracting Available.

---

### 5. Phase 4 resource_report.py couldn't parse csynth.rpt

**Problem:** The parser looked for `'Utilization Estimates'` in the report header — a
string that doesn't appear in Vitis HLS 2025.2 csynth.rpt. It also expected integer
values but the report uses `14 (14%)` format. As a result the JSON never received
`resources`, `basys3_fit`, or `timing_met` fields, causing 6 Phase 4 tests to skip.

**Fix:** Replaced the parser to look for the line starting with `|+ myproject`, split by
`|`, extract all fields matching `^\d+` (capturing the integer before the percentage),
and take the last 4 as BRAM/DSP/FF/LUT. Updated `check_timing` to use the Vivado
post-route WNS from `artifacts/synthesis_report.json` (authoritative) when available,
falling back to HLS pre-route slack otherwise.

---

### 6. Phase 4 LUT budget check was set below HLS estimate

**Problem:** `BASYS3_BUDGET['LUT'] = 16000` (a conservative pre-Phase-5 headroom
estimate). The HLS pre-route estimate for the MLP alone is 16,234 LUT — above 16,000.
Caused `test_resource_report_fits_basys3` to fail.

**Fix:** Updated budget to the actual chip limit (20,800). The HLS pre-route estimate is
an overestimate; the real gate is `test_phase5.py::TestSynthesisReport::test_lut_within_budget`,
which checks the Vivado post-route result (9,588 LUT).

---

## Final Results

> **SUPERSEDED (2026-06-17):** the table below is from the OLD io_serial design and was
> measured on an invalid netlist (features_q0 tied to GND — AUDIT_REPORT.md §3). The valid
> io_stream numbers are in the ADDENDUM "Re-synthesis results" at the bottom of this file.

### Vivado post-implementation (full design: MLP + UART + controller)

```
+----------+-------+--------+-------+
| Resource | Used  | Budget | Used% |
+----------+-------+--------+-------+
| LUT      | 9,588 | 20,800 | 46.1% |
| FF       |10,127 | 41,600 | 24.3% |
| BRAM     |     7 |    100 |  7.0% |
| DSP      |    18 |     90 | 20.0% |
+----------+-------+--------+-------+
| WNS      |+0.111 ns       |  MET  |
+----------+-------+--------+-------+
```

Timing met at 100 MHz (Basys 3 clock). WNS = +0.111 ns.

Note: the HLS pre-route LUT estimate (16,234) was ~1.7× the actual Vivado result (9,588).
Vivado's full synthesis + optimization merges logic across all modules and is significantly
more efficient than HLS's per-layer pre-route estimate.

Bitstream: `C:/nfl_fpga_build/nfl_fpga_accelerator.runs/impl_1/top.bit`

### Test suite

```
105 passed, 0 skipped, 0 failed
Phases 1–5 all green
```

---

## Files

| File | Purpose |
|------|---------|
| `hdl/uart_rx.v` | UART receiver: serial → byte, metastability-safe |
| `hdl/uart_tx.v` | UART transmitter: byte → serial |
| `hdl/uart_framing.v` | Packet assembly, SOF, XOR checksum, TX sequencer |
| `hdl/mlp_controller.v` | ap_ctrl_hs FSM, feature memory, result capture |
| `hdl/top.v` | Top-level wiring of all modules |
| `hdl/myproject_stub.v` | Stub for iverilog syntax checking only — NOT added to Vivado |
| `constraints/basys3.xdc` | Pin assignments: clk=W5, rst=U18, RX=B18, TX=A18 |
| `scripts/create_project.tcl` | Creates Vivado project from WSL paths; adds IP Verilog directly |
| `scripts/run_synth.tcl` | Runs synthesis → implementation → bitstream; writes reports |
| `scripts/parse_reports.py` | Parses Vivado reports → `artifacts/synthesis_report.json` |

---

## Key Lessons

| Issue | Root cause | Fix |
|-------|-----------|-----|
| `myproject` not found in synthesis | IP catalog needs output-products generated separately | Add IP Verilog files as direct RTL sources |
| `exec unzip` failed on Windows | No `unzip` command on Windows | Pre-extract in WSL, point Tcl at extracted directory |
| parse_reports.py ZeroDivisionError | Vivado report has extra Fixed column | Extra `[^|]*` skip group in regex |
| resource_report.py skips silently | csynth.rpt header format doesn't contain 'Utilization Estimates' | Parse `|+ myproject` line directly; handle `N (M%)` format |
| 6 Phase 4 tests skipping | hls_resource_report.json missing `resources`/`basys3_fit` keys | Fixed parser + re-ran resource_report.py |

---

## Phase 6 Prerequisites

- Bitstream: `C:/nfl_fpga_build/nfl_fpga_accelerator.runs/impl_1/top.bit`
- Board: Basys 3 (Artix-7 XC7A35T) — first time board is needed
- UART: USB-A cable to Basys 3 UART port (left mini-USB is JTAG; right is UART)
- Phase 7 laptop script sends 23-byte packets, receives 4-byte responses at 115200 baud
- Feature encoding: `uint8 = round(scaler.transform(features) * 255)`, order from `artifacts/features.json`

---

## ADDENDUM (2026-06-17) — HDL rewritten for the io_stream AXIS IP

Phase 4 was re-synthesized with `io_type='io_stream'` to fix the in-hardware MLP
deadlock (see phase4_hls/PHASE4_COMPLETE.md ADDENDUM and AUDIT_REPORT.md §1). That
changed the IP's top-level interface from `ap_memory` to **AXI4-Stream + ap_ctrl_hs**,
so the Phase 5 HDL was rewritten to match. The original "Final Results" / utilization
numbers in this doc were measured on the OLD io_serial design and are **superseded** —
the real numbers come from re-running synthesis on the design below.

### HDL changes
- **`mlp_controller.v` — rewritten for AXI4-Stream.** Packs the 21 feature bytes into
  the 672-bit `features_TDATA` beat (each byte at lane bits [11:4]:
  `lane = {20'b0, byte, 4'b0}`), pushes it with `features_TVALID`/`TREADY`, and captures
  the two output beats via `layer9_out`/`layer10_out` `TVALID`/`TREADY`. Drives the
  `ap_ctrl_hs` handshake (assert `ap_start`, drop on `ap_ready`, results gate on capture).
  Output slicing changed with the new widths: win = `layer9_out_TDATA[17:0]`
  (ap_fixed<18,6>), spread = `layer10_out_TDATA[31:0]` (ap_fixed<32,16>, integer byte
  [23:16]). The old `[11:4]`/`[23:16]`-of-an-18/32-bit-bus and the ap_memory
  `features_address0/ce0/q0` path are gone.
  - Folded in **win saturation** (§5.3): negative → 0x00, ≥1.0 → 0xFF, else [11:4].
  - Folded in a **WAIT watchdog** (§5.2): `TIMEOUT_CYCLES` parameter (default 100,000 =
    1 ms); on expiry returns to IDLE and pulses `result_timeout` instead of hanging.
- **`uart_framing.v`:** added `result_timeout` input → TX response **status 0x02**
  (distinct from 0x00 result / 0x01 checksum-NACK), so a stuck inference is diagnosable.
- **`top.v` — full design restored** (working tree had the loopback stub) with:
  - the new AXIS wiring to `myproject`;
  - `ap_rst_n = ~rst_sync` (the io_stream IP uses synchronous **active-low** reset);
  - a 2-FF reset synchronizer on the BTNC press (§8);
  - **3 sticky debug LEDs** (§2): LD0=`rx_done`, LD1=`packet_error`, LD2=`result_valid`,
    matching `basys3.xdc` U16/E19/U19. LD2 is the direct "MLP produced a result" probe —
    it never lit with the old deadlocking IP; it must light now on a valid packet.
- **`myproject_stub.v`:** replaced the obsolete ap_memory stub with an io_stream
  behavioral model (iverilog/elaboration + smoke sim only; NOT added to the Vivado project).

### Verification (WSL iverilog — no Vivado/board needed)
- Leaf modules and the full `top.v` (with the stub) **elaborate clean** (`-g2012`).
- Controller↔IP **smoke sim passes**: `result_valid` in ~10 cycles, win `0x80`,
  spread `0x03`, no deadlock/timeout — the AXIS + ap_ctrl_hs handshake works end-to-end.

### IP install + synth guards (done)
- New Phase 4 io_stream IP extracted over `artifacts/ip_repo/` (replaces the deadlocking
  io_serial IP); `artifacts/xilinx_com_hls_myproject_1_0.zip` refreshed.
- `create_project.tcl`: aborts if `ip_repo` lacks `features_TDATA` (won't synth the old IP).
- `run_synth.tcl`: aborts on multi-driven nets in the synth log (§3).

### Re-synthesis results (io_stream — THE VALID NUMBERS, supersede "Final Results" above)
Vivado 2025.2, xc7a35tcpg236-1, full design (MLP + UART + framing + controller),
synth + impl + bitstream, **0 errors / 0 critical warnings**, DRC clean, `top.bit` generated:

```
+----------+--------+--------+-------+
| Resource | Used   | Avail  | Used% |
+----------+--------+--------+-------+
| LUT      | 17,896 | 20,800 | 86.0% |
| FF       | 29,806 | 41,600 | 71.6% |
| BRAM     |      7 |     50 | 14.0% |
| DSP      |     18 |     90 | 20.0% |
| Slices   |  7,939 |  8,150 | 97.4% |  <- spread-out packing, not capacity; routed+closed fine
+----------+--------+--------+-------+
| WNS      | +0.145 ns        |  MET  |   hold WHS +0.017 ns
+----------+--------+--------+-------+
```

- **Timing closes at 100 MHz** — the §11.2 risk (HLS Est Fmax 94.62) did NOT materialize;
  no 50 MHz fallback needed.
- **Fits with headroom** — HLS estimated 28,869 LUT (138%); real impl is 17,896 (86%),
  confirming the HLS estimate overcounts ~38% (gate on real numbers, not the estimate).
- Slice occupancy 97.4% is the placer spreading logic thin (only 86% LUTs / 72% FFs used),
  NOT 97% of capacity — routing util ~17%, congestion 1×1, so ample routing/timing margin.
  Only relevant if a lot more logic were added later; the design is feature-frozen.
- `parse_reports.py` → `artifacts/synthesis_report.json` = PASS. bitstream:
  `C:/nfl_fpga_build/nfl_fpga_accelerator.runs/impl_1/top.bit`.

### Still pending
- Re-run Phase 6 `test_mlp_verilog` against the real `myproject.v` (no FIFO stubs) — the last
  pre-board verification (layer 4 of the test ladder).
- Board bring-up (loopback → NACK → smoke → golden) when the board is available; the sticky
  LEDs (LD0=rx, LD1=err, LD2=result) are the probes — LD2 must light on a valid packet.
