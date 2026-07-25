# Phase 6 (conifer) — Simulation & Verification: Complete

> **Status: COMPLETE** — ✅ end-to-end chain golden generated and metric-gated,
> ✅ 100-game XSIM chain regression **bit-exact** (0 timeouts), ✅ full-UART XSIM
> test PASS (3 games bit-exact + NACK + SOF-collision).
> Sign-off gate for `gbdt/phase5_fpga` (which is also COMPLETE — bitstream built).

This is the GBDT analog of `mlp/phase6_sim/functional/` (the honest, real-IP MLP
verification). It follows the same philosophy the MLP audit forced on us:
**no stubs, real synthesized netlists, golden computed on the exact quantized
words the hardware sees.** The MLP suite is untouched; everything here lives in
the `_conifer` sibling folder per the project convention.

---

## What Phase 6 Has to Prove

The board runs a *chain*, not two independent models:

```
21 feats ─► conifer_win (real netlist) ─► margin
                                            │
                              sigmoid ROM ◄─┘   (1024×12, hardware-exact)
                                   │
                              win_prob ─► conifer_spread.x_21
                                              │
         spread = residual + vegas_spread ◄───┘  (one adder)
```

Phase 4's cosim proved each IP alone, against a golden where stage 2 was fed a
**float** win probability from xgboost. That is not what the board does. Phase 6
has to prove three separate things:

1. **The chain is numerically what Python says it is** — including the hardware
   sigmoid ROM, which does not exist in the Python model at all.
2. **The RTL chain (controller + ROM + both real netlists) matches that golden
   bit-for-bit** — no tolerance band.
3. **The whole `top_gbdt` works over real serialized UART** — framing,
   checksum, 24-bit feature reassembly, error paths.

---

## Files

```
gbdt/phase6_sim/
├── make_chain_golden.py        end-to-end fixed-point chain model -> chain_golden/
├── tb_regression_gbdt.v        controller + ROM + BOTH real netlists, 100 games
├── run_xsim_gbdt.bat           XSIM runner for the regression (Windows)
├── check_results.py            bit-exact gate: sim_results.csv vs golden_chain.npy
├── tb_top_uart_gbdt.v          entire top_gbdt driven at 115200 baud, self-checking
├── run_xsim_uart_gbdt.bat      XSIM runner for the full-UART test (Windows)
├── sim_results.csv             last regression output (100 games, committed)
└── chain_golden/
    ├── tb_inputs.mem           100 games × 21 feats, 24-bit two's-complement hex
    ├── golden_fixed.mem        expected (win_prob, spread) words — TB self-check
    ├── golden_chain.npy        margin / win_prob / residual / spread (checker input)
    ├── golden_{margin,win_prob,residual,spread}.dat   human-readable per-stage
    ├── sigmoid_lut.mem         1024×12 ROM contents — THE spec, shared with the HDL
    └── chain_report.json       precision, LUT spec, val metrics, margin range
```

`sigmoid_lut.mem` is deliberately a **single shared artifact**: the golden
generator writes it and `gbdt/phase5_fpga/hdl/sigmoid_rom.v` `$readmemh`s the
same file. There is no way for the model and the ROM to drift.

---

## Layer 1 — The Chain Golden (`make_chain_golden.py`)

Runs the conifer **C++ emulation** (`ap_fixed<24,12>`, same backend the HLS IPs
were generated from) over the full 2021–2022 val set, through the exact hardware
path, then dumps the first 100 games as testbench vectors.

### The hardware sigmoid spec (locked here, implemented verbatim in Verilog)

```
m      = round(margin * 4096)                     # ap_fixed<24,12> integer repr
m      = clip(m, -32768, 32767)                   # margin in [-8.0, +8.0)
index  = (m + 32768) >> 6                         # 0..1023, step 1/64
ROM[i] = round(sigmoid(-8 + (i + 0.5)/64) * 4096) # 12-bit unsigned, midpoint-sampled
win_prob = ROM[index] / 4096
```

Midpoint sampling (`i + 0.5`) rather than left-edge halves the worst-case
quantization error for free — it costs nothing in hardware since it only changes
the ROM contents.

### Sanity gate — chaining costs nothing

| Metric | Chained hardware | Reference (float win_prob into stage 2) |
|---|---|---|
| Win accuracy (val) | **65.38%** | 65.38% (per-stage HW) |
| Spread MAE (val) | **9.7697** | 9.7707 |
| max \|hw_sigmoid − float sigmoid\| | 0.0334 | — |

The chained MAE is *marginally better* than the float-prob reference — noise, but
it confirms the ROM's 0.033 worst-case error is nowhere near enough to move the
model. Observed margin range is **[−1.68, +2.25]**, comfortably inside the ROM's
±8 domain, so the clamp never fires on real data (it exists only for safety).

---

## Layer 2 — XSIM Chain Regression (100 games, real netlists)

`tb_regression_gbdt.v` instantiates `gbdt_controller` + `sigmoid_rom` + the
**real** `conifer_win` and `conifer_spread` synthesized Verilog, feeds
`tb_inputs.mem`, and logs the raw 24-bit result words. `check_results.py` gates
on **exact** equality — no tolerance, because the golden models the hardware
bit-for-bit, so any mismatch is a genuine RTL/spec divergence.

```
=== Phase 6 (conifer) chain regression check: 100 games ===

  timeouts             : 0
  win_prob bit-exact   : 100/100
  spread   bit-exact   : 100/100

PASS — XSIM chain is bit-exact vs the C++ emulation golden.
```

**22 cycles per game** end-to-end (9 + 9 per IP, plus sigmoid ROM read and
handshakes) — vs ~1,494 cycles for the MLP's real-IP regression.

Contrast with the MLP: `mlp/phase6_sim/functional/check_results.py` needed a ±3-count
tolerance and a "excuse the coin-flip games" rule, because the MLP returns
quantized *bytes* and QKeras only approximates fixed-point in float32. The GBDT
returns full 24-bit words and the conifer C++ emulation is the same arithmetic
the netlist implements — so we get to demand exactness. That is a much stronger
gate, and it is what caught the bug below.

---

## Layer 3 — Full-UART XSIM Test (entire `top_gbdt`)

`tb_top_uart_gbdt.v` drives the whole design the way the laptop will: it
bit-bangs the 65-byte request into `uart_rxd` at 115200 baud (868 cycles/bit),
lets RX → framing → controller → both IPs → framing TX run, deserializes the
8-byte response from `uart_txd`, and self-checks against `golden_fixed.mem`.

| Scenario | Expectation | Result |
|---|---|---|
| 3 good packets | `SOF=0x55`, win_prob + spread words **bit-exact**, status `0x00`, LEDs correct | PASS |
| Corrupted checksum | status `0x01` (NACK) | PASS |
| SOF-collision — all 63 feature bytes = `0xAA` | framing stays in `RECV_FEATURES`, treats them as data, status `0x00` | PASS |

```
FULL-UART TEST (GBDT): PASS (0 failures)
```

The SOF-collision case matters because the GBDT protocol sends **raw** 24-bit
features, so `0xAA` (the request SOF marker) is a perfectly ordinary feature
byte — unlike the MLP's scaled INT8 packet where it was rarer. A framing FSM that
re-syncs on any `0xAA` would corrupt real games in the field.

Two independent testbenches (Layer 2 and Layer 3) now agree on the hardware's
output through completely different stimulus paths.

---

## Challenges Encountered

### 1. The round-vs-truncate 1-ulp bug — caught before hardware

**Symptom:** the first chain golden disagreed with XSIM on a handful of games by
exactly 1 ulp, with no pattern.

**Root cause:** the golden was computed on **raw float** features. The conifer
C++ emulation converts them to `ap_fixed<24,12>` by **truncating** (AP_TRN),
while the UART protocol sends `round(raw * 4096)` — the host **rounds**. For any
feature landing in the upper half of a quantization step, the emulation saw a
value 1 ulp below what the hardware would see. A tree comparing against a
threshold near that boundary then takes a different branch, and the outputs
diverge by a whole leaf value.

**Fix:** `make_chain_golden.py` pre-quantizes inputs with `quant()` —
`round(x * 4096) / 4096` — *before* anything else runs, so the emulation, the
`.mem` file, and the eventual UART bytes all carry identical words.

**Why this mattered:** on the board this would have shown up as "mysterious"
occasional mismatches with no way to tell a golden bug from an RTL bug. The
zero-tolerance gate is what made it visible at all; a ±3 tolerance band would
have silently swallowed it.

### 2. The per-stage golden was the wrong golden

Phase 4's `make_tb_data.py` feeds stage 2 a float win probability straight from
xgboost. That verifies each IP in isolation, which is what it's for — but it
cannot verify the chain, because the board feeds stage 2 a value that went
through stage 1's fixed-point margin *and* a 1024-entry ROM. Phase 6 needed its
own golden that models the full path. Splitting them (per-stage goldens gate the
HLS flow, chain golden gates the wrapper) mirrors the MLP precedent where
`gen_vectors.py` lives in `mlp/phase6_sim/functional/`, not phase 4.

### 3. The sigmoid ROM has no Python counterpart

The Python model calls `sigmoid()`. The hardware reads a ROM. There is nothing
to "compare against" unless the spec is written down exactly once and both sides
derive from it. Resolution: the golden generator *is* the spec (the docstring at
the top of `make_chain_golden.py`), and it emits `sigmoid_lut.mem` which the
Verilog ROM loads directly. Any future change to precision or LUT size
regenerates both together.

### 4. UART receiver timing in the testbench

Deserializing `uart_txd` by hunting for each byte's start bit is fragile when the
payload is mostly low bytes — a `0x00` data byte looks like a start bit. Adopted
the MLP TB's strategy: lock to the **SOF start edge once**, then sample all 8
frames at fixed `CPB`/`CPB+CPB/2` offsets. Also had to size the arm window
(`RXWAIT = 800,000` cycles): the DUT cannot answer until the entire 65-byte
request has clocked in, which is ~565k cycles at 8,680 cycles/byte — a naive
timeout fires long before the design has done anything wrong.

### 5. Conifer IP interface quirks

Wiring the real netlists into the testbench surfaced two things the HLS report
doesn't advertise: **`ap_rst` is active-HIGH** (hls4ml's MLP IP used `ap_rst_n`),
and **`score_1` is a dead template input port** — never read internally by either
IP, tied to `24'd0`. Getting the reset polarity wrong produces an IP that simply
never leaves idle, which looks like a controller bug.

### 6. Toolchain friction

- **XSIM, not iverilog.** Same reason as the MLP functional suite: the real
  synthesized netlists use constructs iverilog chokes on. Both runners are
  Windows `.bat` files staging sources into `C:\Temp\...`, with the WSL tree
  mounted over UNC.
- **`xvlog` does not expand `*.v` on Windows.** The runners build an explicit
  file list (`xvlog_files.f`) and pass it with `-f`.
- `xelab -relax` is required for the generated netlists to elaborate.

---

## How to Re-Run

```bash
# 1. Regenerate the chain golden (WSL, after gbdt/phase4_hls/make_tb_data.py)
source /home/younix/nfl-fpga-accelerator/venv/bin/activate
cd /home/younix/nfl-fpga-accelerator
python gbdt/phase6_sim/make_chain_golden.py
```

```bat
REM 2. Chain regression (Windows, Vivado 2025.2 XSIM). Optional arg = #games.
\\wsl.localhost\Ubuntu\home\younix\nfl-fpga-accelerator\gbdt/phase6_sim\run_xsim_gbdt.bat 100

REM 3. Full-UART test (Windows). Self-checking.
\\wsl.localhost\Ubuntu\home\younix\nfl-fpga-accelerator\gbdt/phase6_sim\run_xsim_uart_gbdt.bat
```

```bash
# 4. Gate the regression (WSL)
python gbdt/phase6_sim/check_results.py     # exit 0 = PASS
```

Step 3 is self-checking — look for `FULL-UART TEST (GBDT): PASS`.

---

## Known Gaps (deliberate, not blockers)

| Gap | Assessment |
|---|---|
| No leaf-level unit tests for `uart_framing_conifer.v` | It is new HDL, covered only end-to-end by Layer 3 (including the NACK and SOF-collision paths — the two things unit tests would target). `uart_rx.v` / `uart_tx.v` are **unchanged** and still covered by the MLP's cocotb unit tests in `mlp/phase6_sim/cocotb/`. |
| No `tests/test_phase6_conifer.py` pytest | The gate is `check_results.py` (exits non-zero on failure) plus the self-checking UART TB. Worth wrapping when the `_conifer` folders are merged back into the main tree. |
| Chain golden covers 100 of ~570 val games as TB vectors | The *metric* gate runs the full val set; only the XSIM vectors are capped at 100 for sim runtime. |

---

## Phase 6 (conifer) Status: COMPLETE

The two-stage GBDT chain is verified at every level the toolchain allows without
hardware, and to a **stricter** standard than the MLP flow achieved (exact
equality vs a tolerance band). This gated `gbdt/phase5_fpga`, which is also
complete: 12,095 LUTs (58%), 0 DSPs, WNS +0.965 ns, bitstream built.

**Next:** `gbdt/phase7_deploy/` — pyserial harness for the 65-byte packet on
COM8, verified bit-exact against `chain_golden/golden_fixed.mem` on the real board.
