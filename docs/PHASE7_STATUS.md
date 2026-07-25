# Phase 7 — Board Deployment Status

> [!WARNING]
> **SUPERSEDED — historical snapshot, do not use as a reference.**
>
> This was written mid-debug, while `top.v` was still a temporary loopback stub and the
> UART pin orientation (A18 vs B18) was unresolved. Everything here was overtaken by the
> actual bring-up. It is kept only as a record of what the project looked like before the
> board worked.
>
> For the real, verified state see:
> - **[`mlp/phase7_deploy/PHASE7_COMPLETE.md`](../mlp/phase7_deploy/PHASE7_COMPLETE.md)** —
>   MLP sign-off: 50/50 bit-exact on hardware, B18=RX resolved, 11.5 → 3.4 ms latency fix.
> - **[`gbdt/phase7_deploy/PHASE7_CONIFER_COMPLETE.md`](../gbdt/phase7_deploy/PHASE7_CONIFER_COMPLETE.md)** —
>   GBDT sign-off: 100/100 bit-exact with zero tolerance, median 6.9 ms.
>
> Note also that paths below were mechanically updated by the 2026-07-26 `mlp/`+`gbdt/`
> reorg, so they point at current locations even though the content is obsolete.

## What's Done

### Python files (all written, all non-board tests pass)

| File | Status |
|---|---|
| `mlp/phase7_deploy/inference/fpga_client.py` | Done |
| `mlp/phase7_deploy/inference/feature_builder.py` | Done (elo_diff/is_dome/is_div_game bugs fixed vs plan) |
| `mlp/phase7_deploy/inference/logger.py` | Done |
| `mlp/phase7_deploy/board/verify_uart.py` | Done (fixed JSON/CSV bug from plan) |
| `mlp/phase7_deploy/board/program_board.py` | Done (fixed WSL2 TCL path + cmd.exe wrapper) |
| `mlp/phase7_deploy/validation/golden_vector_test.py` | Done |
| `mlp/phase7_deploy/ui/app.py` | Done |
| `tests/test_phase7.py` | Done — 17/17 non-board tests pass |

Run tests: `venv/bin/python3 -m pytest tests/test_phase7.py -v -k "not board"`

---

### HDL bugs found and fixed

#### 1. `mlp_controller.v` — multi-driver on `features_q0` (CRITICAL)
- **Bug**: Two `always @(posedge clk)` blocks both drove `features_q0`.
  Vivado resolved the conflict by keeping the constant GND driver and discarding
  the read logic → MLP always received 0 for every feature → garbage output.
- **Fix**: Moved the `features_q0 <= 18'd0` reset into the feature-read always
  block. Reset now lives in one place:
  ```verilog
  always @(posedge clk) begin
      if (rst)
          features_q0 <= 18'd0;
      else if (features_ce0)
          features_q0 <= {6'b0, feature_store[features_address0], 4'b0};
  end
  ```
- **Commit message written**: "Fix multi-driven reg bug in mlp_controller — features_q0 was always zero"

#### 2. `uart_framing.v` — `!tx_start` guard (Phase 6 change, confirmed correct)
- The `&& !tx_start` guard in TX_WIN, TX_SPREAD, TX_STATUS is intentional and correct.
- The guard prevents the TX FSM from advancing before uart_tx's busy signal rises.
- Phase 5 bitstream was stale — re-synthesis required (done).

---

### Hardware debugging in progress

#### Board setup
- Basys 3 has ONE micro-USB port handling both JTAG (channel A) and UART (channel B) via FT2232HQ chip.
- On Windows: Vivado uses JTAG, COM8 = UART channel B.
- On Linux/WSL2: usbipd forwards device → ttyUSB0 (channel A), ttyUSB1 (channel B).
- **Recommendation: run all board tests from Windows Python on COM8** (WSL2/usbipd adds unnecessary complexity).

#### COM port stability issue
- Board was dropping COM8 intermittently → caused by loose USB connection.
- Resolved by replugging. Board now shows Digilent demo (counting on 7-seg displays) on power-up = healthy board.
- **Remember**: FPGA bitstream is volatile. Every power cycle reloads the Digilent factory demo from flash. Must reprogram via Vivado Hardware Manager each session.

#### Pin assignment saga (unresolved)
The A18/B18 UART pin assignment has been flipped back and forth. Current state:
- **Current XDC**: `uart_rxd = B18`, `uart_txd = A18` (reverted to original)
- The top.v comment line 4 says `B18=RX, A18=TX` which matches the original.

History of attempts:
| XDC | Test | Result |
|---|---|---|
| B18=rxd, A18=txd (original) | Smoke test | Timeout — but features_q0 bug also present at this point |
| A18=rxd, B18=txd (my swap) | NACK test (bad checksum) | 0 bytes — sticky LD0 never lit |
| A18=rxd, B18=txd | Windows COM8 test | 0 bytes |
| B18=rxd, A18=txd (reverted) | Loopback test | **NOT YET RUN** |

**Key finding**: Sticky LD0 (wired to `rx_done`) never lit with A18=rxd.
This means FPGA received zero bytes on A18 → A18 is likely the wrong RX pin.

---

### Current state of HDL (loopback test)

`top.v` is currently a **loopback stub** — all real logic removed:
```verilog
assign uart_txd = uart_rxd;   // direct loopback
// LD0 latches when uart_rxd goes low (start bit)
```

XDC: `B18=rxd, A18=txd` (original/reverted).

**Loopback test not yet run.** This is the immediate next step.

---

## Immediate Next Steps

### Step 1 — Run loopback test (confirms correct pin)

1. Synthesize + program board (loopback top.v is already written)
2. From Windows PowerShell:
   ```powershell
   python \\wsl.localhost\Ubuntu\home\younix\nfl-fpga-accelerator\test_win.py
   ```
   (`test_win.py` sends 23 bytes to COM8 with bad checksum, reads 4 bytes back)
3. Watch LD0 on board

**Interpret results:**
- Bytes come back + LD0 lit → B18=rxd is correct, UART pins confirmed. Restore full design.
- Bytes come back + LD0 dark → B18=rxd correct but LED logic needs fixing
- 0 bytes → swap to A18=rxd in XDC, re-synthesize, retry

### Step 2 — Restore full design

Once loopback confirms correct pins, restore `top.v` from git (or rewrite from plan).
All HDL fixes must be present:
- `features_q0` single-driver fix in `mlp_controller.v` ✓
- `!tx_start` guard in `uart_framing.v` ✓
- Correct pin assignment in `basys3.xdc` (TBD from loopback result)
- Sticky LEDs can stay for now

### Step 3 — NACK test (verifies framing layer)

```powershell
python \\wsl.localhost\Ubuntu\home\younix\nfl-fpga-accelerator\test_win.py
```
Should return `Got 4 bytes: 55000001` (SOF + zeros + NACK status).

### Step 4 — Smoke test

```powershell
python \\wsl.localhost\Ubuntu\home\younix\nfl-fpga-accelerator\mlp/phase7_deploy\board\verify_uart.py COM8
```

### Step 5 — Golden vector test

```powershell
python \\wsl.localhost\Ubuntu\home\younix\nfl-fpga-accelerator\mlp/phase7_deploy\validation\golden_vector_test.py COM8
```
Expect ≥98% pass rate (49-50/50 games).

### Step 6 — Board pytest

From WSL2 (or Windows with pytest installed):
```bash
FPGA_PORT=COM8 pytest tests/test_phase7.py -v
```

### Step 7 — Commit everything

Two commits:
1. HDL fixes: `mlp_controller.v` features_q0 bug + pin assignment fix + uart_framing.v guard
2. Python deployment layer: all mlp/phase7_deploy files + tests passing

---

## Key Files Modified This Session

| File | What Changed |
|---|---|
| `mlp/phase5_fpga/hdl/mlp_controller.v` | Fixed features_q0 multi-driver bug |
| `mlp/phase5_fpga/hdl/uart_framing.v` | !tx_start guard confirmed correct (Phase 6 change) |
| `mlp/phase5_fpga/hdl/top.v` | Currently loopback stub — needs restoration after pin confirmed |
| `mlp/phase5_fpga/constraints/basys3.xdc` | Currently B18=rxd, A18=txd — needs confirmation from loopback |
| `mlp/phase7_deploy/` | All Python files written |
| `tests/test_phase7.py` | Written, 17/17 non-board tests pass |
| `requirements.txt` | Added pyserial |

---

## Notes

- **Never refit `scaler.pkl`** — loaded as-is in feature_builder.py
- **Feature order is permanent** — 21 features, locked in `artifacts/features.json`
- **Board must be reprogrammed** after every power cycle (bitstream is volatile)
- **Install on Windows Python** for board testing: `pip install pandas numpy scikit-learn pyarrow pyserial`
- `test_win.py` and `test_uart_raw.py` are temporary debug scripts — delete before final commit
