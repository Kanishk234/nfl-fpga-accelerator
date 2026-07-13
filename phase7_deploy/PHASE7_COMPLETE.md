# Phase 7 Complete: Board Deployment & On-Silicon Inference

> Supersedes `PHASE7_STATUS.md` (a mid-debug snapshot: it captured the design while
> `top.v` was a temporary loopback stub with the UART pins unresolved). This document
> is the sign-off — the board is programmed, inference runs on real hardware, and the
> output is bit-exact against the Phase 6 simulation.

## What Phase 7 Does

Phase 7 is the laptop side of the accelerator and the actual board bring-up. It:

1. **Programs** the Basys 3 with the Phase 5 bitstream (`top.bit`).
2. **Builds features** from the real NFL dataset — looks up a historical game (or an
   upcoming matchup), scales it with the *frozen* `scaler.pkl`, and encodes 21 `uint8`
   bytes in the locked `features.json` order.
3. **Runs inference on the FPGA** — sends the 23-byte request over USB-UART with `pyserial`,
   the board computes the MLP in hardware, and returns the 4-byte response.
4. **Decodes, logs, and displays** the result — win probability + point spread, with a
   Tkinter GUI (`ui/app.py`), a CSV audit log, and validation scripts.

The headline result: **the Basys 3 runs the trained neural network in silicon and its
predictions match the simulation exactly** (50/50 games, zero deviation). The FPGA is
computing the MLP — not replaying a stored table (proven below).

---

## Communication Protocol (unchanged from Phase 5)

### Laptop → FPGA — 23 bytes
```
Byte 0:      0xAA           — start-of-frame
Bytes 1–21:  feature[0..20] — uint8, one per feature, in features.json order
Byte 22:     checksum       — XOR of bytes 1–21
```

### FPGA → Laptop — 4 bytes
```
Byte 0:  0x55        — start-of-frame (distinct from 0xAA)
Byte 1:  win_prob    — uint8; win probability = byte / 256.0
Byte 2:  spread_int  — signed int8, point spread in whole points
Byte 3:  status      — 0x00=OK, 0x01=checksum NACK, 0x02=MLP watchdog timeout
```

Round-trip measured on the board: **~11.5 ms out of the box, ~3.4 ms after tuning** — the MLP core
itself is only 591 cycles (~5.9 µs); the rest is UART + USB-bridge overhead. See
[Latency Optimization](#latency-optimization--115-ms--34-ms-the-bottleneck-was-not-what-it-looked-like).

---

## Architecture — `phase7_deploy/`

```
phase7_deploy/
├── board/
│   ├── program_board.py     — flashes top.bit via Vivado batch (or use the IDE GUI)
│   └── verify_uart.py        — one-packet smoke test; run on every reconnect
├── inference/
│   ├── fpga_client.py        — FPGAClient: connect, frame, checksum, send, decode
│   ├── feature_builder.py    — real NFL game → 21 scaled uint8 bytes (frozen scaler)
│   └── logger.py             — appends each prediction to logs/inference_log.csv
├── validation/
│   └── golden_vector_test.py — drives all 50 Phase-6 games, checks bit-exact vs sim
├── ui/
│   └── app.py                — Tkinter GUI: pick a game, predict, live history
└── logs/inference_log.csv    — audit trail
```

### fpga_client.py — the host driver
`FPGAClient.run_inference(feature_bytes)` validates the 21 bytes, computes the XOR
checksum, writes `0xAA + features + checksum`, reads exactly 4 bytes, and decodes:
`win_prob = win_raw / 256.0`, `spread = signed int8`. It raises on a short read
(board hung → press BTNC), a bad SOF (framing desync), or an out-of-range input.
`flush_host_buffers()` deliberately does **not** send a UART break to "reset" the board
(the old reset path injected `0x00` bytes that the framing FSM misread — AUDIT §7.1);
only the physical BTNC resets the design.

### feature_builder.py — data → bytes
Loads `artifacts/features.json` (21-feature locked order) and `artifacts/scaler.pkl`
(**never refit**), reads `data/processed/games.parquet`, and encodes any game with the
exact hardware quantization: `clip(round(scaler.transform(x) * 255), 0, 255)`. This is
byte-identical to how the Phase 6 golden vectors were generated, so the board receives
precisely what simulation verified (pinned by `test_host_encoding_matches_sim_vectors`).
`from_current_week()` builds an upcoming matchup from each team's most recent stats and
recomputes matchup-specific features (`elo_diff`, `is_dome`, `is_div_game`).

### program_board.py / GUI
Writes a Hardware-Manager TCL and invokes Vivado in batch mode. In practice, programming
was done through the **Vivado IDE Hardware Manager** (Open target → Auto Connect →
Program device → `top.bit`) — simpler and no path assumptions. The bitstream is volatile:
every power cycle reloads the Digilent factory demo (7-seg counting), so the board must
be reprogrammed each session.

---

## Live UI — the game-picker predictor

`ui/app.py` is a Tkinter desktop app: pick a **season**, optionally filter by **home/away
team**, choose a **game**, hit **Run Inference**, and the board returns the win probability
(shown as a meter) and point spread (in betting notation — the favorite lays the points).
An `Actual: <team> won ✓/✗` line grades the prediction against the real result, and a small
"FPGA Pipeline" strip lights up `Encode → TX 23B → FPGA MLP → RX 4B` as the inference runs.

### The environment problem this design solves

Feature encoding needs the **pandas/scikit-learn** data stack + the frozen scaler, which
live only in the **WSL venv**. The board's UART enumerates as **COM8 on Windows**. So the
obvious "one app that builds features and talks to the board" can run in *neither*
environment — WSL can't easily reach COM8, and the Windows Python has no data stack.

**Fix — split the work across the boundary, once:**

```
  WSL (has pandas + scaler)                  Windows (has COM port + pyserial)
  ┌───────────────────────┐                  ┌────────────────────────────┐
  │ export_catalog.py      │  games_catalog   │ ui/app.py (pyserial-only)  │
  │  scaler.transform →     │ ───.json──────▶ │  load catalog → pick game  │
  │  clip(round(x*255))     │  (21 bytes/game) │  → FPGAClient → board      │
  └───────────────────────┘                  └────────────────────────────┘
```

`export_catalog.py` (WSL) precomputes every game's 21 feature bytes — via the *same*
`FeatureBuilder._encode_row`, so they are byte-identical to the sim/golden vectors — into
`games_catalog.json`. `ui/app.py` (Windows) then needs **only pyserial**: it reads the
catalog, and on "Run" sends the stored bytes to the board. The data stack and the serial
stack never have to coexist in one interpreter. Regenerate the catalog only when the
dataset changes.

### Two front-ends, one backend

Both UIs read the same `games_catalog.json` and drive the board through the same
`FPGAClient`; the shared catalog reader lives in `inference/game_catalog.py`.

- **`ui/app.py` — Tkinter desktop app.** Native window, no browser. Good for a quick local run.
- **`ui/webapp.py` + `ui/index.html` — local web app** (the nicer-looking one). The backend
  is the Python **standard library only** (`http.server`) plus pyserial — no Flask, no extra
  installs. It serves a dark-themed page with an animated SVG win-probability gauge, a live
  `Encode → TX → FPGA MLP → RX` pipeline stepper, betting-notation spread, a correct/missed
  badge, and a history table. It **auto-selects the board's `USB Serial` COM port** so the
  Bluetooth ports can't be picked by mistake, and surfaces board errors (timeout / port busy)
  as an inline message. Launch: `python phase7_deploy/ui/webapp.py` → auto-opens
  `http://127.0.0.1:8713`.

  API (localhost): `GET /api/bootstrap` returns every game's metadata (no feature bytes —
  those stay server-side, looked up by a stable `gid`); `POST /api/infer {port, gid}` runs the
  game on the board and returns win/spread/status/latency. One serial connection is held and
  guarded by a lock so overlapping requests serialize.

### Reading the response
The board's win byte is displayed raw as `raw win N/256`: byte 1 of the 4-byte response is an
8-bit fixed-point fraction, so `win_prob = N / 256` is the **home team's** win probability
(e.g. `177/256 = 69.1%` → home favored; `75/256 = 29.3%` → home is the underdog). Showing the
raw byte makes clear the number came off the chip, not the laptop.

### Verified (headless, real board)
Both paths were tested without opening a window/browser. The Tkinter path: `GameCatalog`
imports with only pyserial, the KC(H)-vs-DET(A) 2023 game filters correctly, its catalog bytes
match `tb_inputs.mem` game 0 exactly, and the UI's own `FPGAClient` returned **177/256 = 69.1%,
spread +5, OK**. The web path: the running server's `GET /api/bootstrap` returned all 6,427
games and `POST /api/infer` on COM8 returned the same **177/256, +5, OK, ~14 ms** — the sim
golden. (The model favors KC; DET actually won the opener → both UIs show `✗`, an honest miss.)

---

## Issues Encountered and Fixes

### 1. UART pin orientation (A18 vs B18) — unresolved through Phase 5/6, settled on hardware

**Problem:** The USB-UART side of the Basys 3 was ambiguous — earlier debugging had flipped
`uart_rxd`/`uart_txd` between A18 and B18 several times, and with no board available it was
never confirmed. A wrong RX pin means the FPGA receives zero bytes and never responds.

**Fix / resolution:** With `basys3.xdc` set to **`uart_rxd = B18`, `uart_txd = A18`**, the
board received all 23 request bytes and returned a well-formed 4-byte response on the very
first smoke test. A wrong RX pin gives 0 bytes back, so a valid response *is* the proof —
**B18=RX / A18=TX is correct.** The long-standing pin question is closed.

### 2. `program_board.py` assumed a Windows Vivado path that doesn't exist

**Problem:** The script hardcodes `C:/Xilinx/Vivado/2025.2/bin/vivado.bat` and a `/mnt/c`
WSL mirror. On this machine Vivado is not at that path (it's reachable another way), so the
scripted flash aborts on the exists-check / missing executable.

**Fix:** Programmed via the **Vivado IDE Hardware Manager GUI** instead — no path
assumptions, and you watch the board while it loads. `program_board.py` remains as a
convenience for whoever has Vivado on the documented Windows path.

### 3. Deployment/validation scripts pointed at vectors that never existed

**Problem:** `verify_uart.py` and `validation/golden_vector_test.py` read
`phase6_sim/test_vectors/input_games.csv` + `expected_outputs.csv`. That directory was
deleted in the Phase 6 post-audit cleanup; the authoritative vectors live in
`phase6_sim/functional/` with a different schema (a hex `$readmemh` file, not a feature-per-
column CSV). Run as-is, both scripts crash.

**Fix:** Repointed both to `phase6_sim/functional/`:
- inputs from `tb_inputs.mem` (50 games × 21 hex bytes/line),
- team names + float reference from `golden.csv`,
- expected board bytes from `sim_results.csv` (see issue 4).

Both now run and pass (`verify_uart.py` → KC vs DET 69.1%; golden test → 50/50).

### 4. Wrong comparison reference — float golden vs the RTL golden

**Problem:** The original `golden_vector_test.py` compared the board against the *float*
model's bytes with a ±13-count tolerance. But the float golden differs from the hardware by
the known input-quantization delta (up to ~25 win counts) — so that comparison would report
spurious failures on some games and mask real regressions on others.

**Fix:** The board runs the **identical RTL** that XSIM simulated, so the correct reference
is `sim_results.csv` (the XSIM output), and the expected difference is **zero**. Tolerance
tightened to ±2 win / ±1 spread (the float `golden.csv` is still displayed for context). The
board then matched sim **bit-exact** — which a loose float comparison could never have shown.

### 5. Host/board environment split (WSL vs Windows)

**Problem:** The board's UART enumerates as **COM8** on Windows (channel B of the FT2232);
reaching it from WSL2 needs `usbipd` forwarding. Meanwhile the data stack lives in WSL —
Windows Python (3.14) has `pyserial` but **not** `pandas`/`numpy`/`scikit-learn`.

**Fix / convention:** Run raw UART tests (client, smoke, golden — `pyserial`-only) from
**Windows on COM8**; run anything needing the data stack (`feature_builder`, the UI) from
**WSL**. Only one process may hold COM8 at a time. Documented so bring-up isn't re-derived.

### 6. Windows console can't encode the report's Unicode

**Problem:** `golden_vector_test.py` printed `Δ` (U+0394) and `≥` (U+2265) headers; the
Windows console is cp1252 and raised `UnicodeEncodeError`, killing the run mid-report even
though the board data was fine.

**Fix:** Replaced with ASCII (`dW`/`dS`, `>=`). The script now runs on both Windows and WSL.

### Carry-over HDL fixes (resolved before bring-up, recorded for the record)

These were found during Phase 7 debugging but are properties of the HDL that were fixed and
folded into the synthesized bitstream:
- **`mlp_controller.v` `features_q0` multi-driver (AUDIT §3):** in the old `ap_memory`
  design two `always` blocks drove `features_q0`, so Vivado kept the GND driver and the MLP
  saw all-zero features. This is moot in the shipped design — the io_stream rewrite replaced
  the whole `features_address0/ce0/q0` memory path with a single AXI-Stream `features_TDATA`
  beat, so the multi-driver structure no longer exists.
- **`uart_framing.v` `!tx_start` guard:** confirmed correct in the current TX FSM (it holds
  the state until `uart_tx` raises busy); the Phase 6 stall was a different, already-fixed
  guard.

---

## Final Results (2026-07-13, on the Basys 3)

### Smoke test — first contact
Synthetic all-`128` features → response `55 90 02 00`:
`SOF 0x55 ✓ · win 144/256 = 56.2% · spread +2 · status OK · 12.3 ms`. The full chain
(UART RX → framing → checksum → controller → io_stream IP → UART TX) works in silicon.

### 50-game golden regression — bit-exact vs simulation
Every Phase-6 game driven through the board, compared to `sim_results.csv`:

```
games = 50
board-vs-SIM:  win |max| = 0   mean = 0.00      spread |max| = 0
timeouts = 0   bad SOF/status = 0
Result: 50/50 PASS  (100.0%)
```

The board reproduces the validated RTL **byte-for-byte** — e.g. KC beats CHI 220/256 = 86%
spread +11; ARI/DAL a 72/256 pick'em — identical to XSIM in all 50 cases. The io_serial
in-hardware deadlock that motivated the whole io_stream rewrite (AUDIT §1) is definitively
gone: **zero timeouts** on real silicon.

### Proof of computation (not a lookup table)
Sweeping feature[0] from 0→255 with the other 20 features held fixed moves the output
**continuously**, which a stored table cannot do (none of these hand-made inputs exist in
any dataset):

```
feat[0]:    0     40     80    120    160    200    255
win% :   50.0%  56.2%  59.8%  63.3%  66.8%  71.1%  73.8%
spread:    -1     +2     +2     +3     +4     +5     +6
```

The FPGA is evaluating the MLP (matrix-multiplies on 18 DSPs, ReLU comparators, sigmoid)
for 1,494 cycles per inference — it is genuinely running the model.

### Test suite
`tests/test_phase7.py` — 17/17 non-board tests pass (decode/encode, checksum, feature
builder, encoding-matches-sim, vectors present). The 4 board tests (`@BOARD_REQUIRED`,
gated on `FPGA_PORT`) exercise connect / smoke / NACK / 20-game golden on real hardware.

---

## Latency Optimization — 11.5 ms → 3.4 ms (the bottleneck was not what it looked like)

The round-trip felt slow (~12 ms) for what is a ~6 µs computation, so it was worth profiling.
A 25-shot measurement (all-`128` features, `pyserial`) showed a suspiciously **constant** ~11.4 ms:

```
before:  n=25  min 10.60  mean 11.57  median 11.43  max 14.47  ms
```

### Where the time actually goes
The tight cluster is the tell — a *fixed* overhead, not compute or line-rate variance:

| Component | Time | Note |
|---|---:|---|
| UART line time | ~2.3 ms | 27 bytes × 10 bits ÷ 115200 baud |
| FPGA MLP compute | ~0.006 ms | 591 cycles @ 100 MHz — negligible |
| **USB bridge + host overhead** | **~9 ms** | the real cost — **constant** |

The ~9 ms is the **FT2232 USB-UART bridge's latency timer**. The chip doesn't forward received
bytes to the host immediately — it waits until a USB buffer fills (62 bytes) *or* a timer expires.
That timer **defaults to 16 ms**, and the 4-byte response is always a partial buffer, so it sits
waiting. The bottleneck was neither the protocol nor the baud rate — it was a USB driver setting.
(Notably, switching to SPI would **not** have helped: the data still crosses the same USB link.)

### The fix
Set the FTDI latency timer **16 ms → 1 ms** — Device Manager → COM8 → Port Settings → Advanced →
*Latency Timer*, or the registry value it writes:

```
HKLM\SYSTEM\CurrentControlSet\Enum\FTDIBUS\VID_0403+PID_6010+<serial>\0000\Device Parameters
    LatencyTimer (DWORD) = 1
```

No HDL change, no re-synthesis, no re-flash. The setting is persistent (registry, keyed to the
board's serial) so it survives reboots and follows the board.

### Result
```
after:   n=25  min 2.66  mean 3.46  median 3.43  max 4.92  ms      →  ~3.3× faster
```
Inference remained **bit-exact** (KC 177/256, spread +5, OK) — only faster. The UART line time
(~2.3 ms) is now the dominant term.

### Next lever (not taken — already imperceptible)
Raising the baud rate **115200 → 1,000,000** (change `CLKS_PER_BIT` 868 → 100 in `uart_rx.v` /
`uart_tx.v`, and `baud` in `fpga_client.py`; 100 MHz ÷ 1 Mbaud = a clean 100 clocks/bit) would cut
the line term ~2.3 ms → ~0.3 ms and land near ~1–1.5 ms. It requires a re-synthesis and buys a
sub-millisecond gain no human notices, so it was left as a documented option rather than done.

> **Takeaway:** profile before optimizing. The obvious suspect (baud rate) was ~20% of the latency;
> the real cost was a USB bridge default that a one-line, no-rebuild change fixed for a 3.3× win.

---

## Files

| File | Purpose |
|------|---------|
| `inference/fpga_client.py` | Host UART driver: frame, checksum, send, decode 4-byte response |
| `inference/feature_builder.py` | NFL game → 21 scaled uint8 bytes (frozen scaler, locked order) |
| `inference/game_catalog.py` | Shared read-only view over `games_catalog.json` (both UIs) |
| `inference/logger.py` | Appends each prediction to `logs/inference_log.csv` |
| `export_catalog.py` | WSL: precompute all games' feature bytes → `games_catalog.json` |
| `board/verify_uart.py` | One-packet smoke test (repointed to `functional/` vectors) |
| `board/program_board.py` | Flash `top.bit` via Vivado batch (or use the IDE Hardware Manager) |
| `validation/golden_vector_test.py` | 50-game on-board regression, bit-exact vs `sim_results.csv` |
| `ui/app.py` | Tkinter desktop GUI — pick a game, predict on the FPGA, live history |
| `ui/webapp.py` + `ui/index.html` | Local web UI (stdlib http.server + pyserial); gauge, pipeline, history |
| `tests/test_phase7.py` | 17 host tests + 4 board tests (`FPGA_PORT`-gated) |

---

## How to Run It

```powershell
# 1. Program the board — Vivado IDE → Open Hardware Manager → Auto Connect →
#    Program device → C:\nfl_fpga_build\nfl_fpga_accelerator.runs\impl_1\top.bit
#    (7-seg counting demo must STOP = our bitstream loaded)

# 2. Smoke test (Windows, COM8 — close any other program holding the port)
python phase7_deploy\board\verify_uart.py COM8

# 3. Full 50-game on-board validation
python phase7_deploy\validation\golden_vector_test.py COM8

# 4. Live GUI (Windows) — one-time catalog build in WSL first:
#      wsl bash -lc "cd ~/nfl-fpga-accelerator && source venv/bin/activate && \
#                    python phase7_deploy/export_catalog.py"
#    then run the pyserial-only UI natively on Windows:
python phase7_deploy\ui\app.py
```

```bash
# Host tests (WSL): 17 pass without a board
pytest tests/test_phase7.py -v -k "not board"
# With the board attached, include the hardware tests:
FPGA_PORT=COM8 pytest tests/test_phase7.py -v
```

---

## Key Lessons

| Issue | Root cause | Fix |
|-------|-----------|-----|
| No UART response | RX pin ambiguity (A18 vs B18) | B18=RX confirmed on hardware — a valid reply proves the RX pin |
| Scripted flash aborts | `program_board.py` assumes a Windows Vivado path that isn't present | Program via Vivado IDE Hardware Manager |
| Validation scripts crash | Point at `test_vectors/` deleted in Phase 6 cleanup | Repoint to `phase6_sim/functional/` (`tb_inputs.mem`/`sim_results.csv`) |
| Spurious pass/fail | Compared board to the *float* golden (±13) | Compare to the RTL golden `sim_results.csv` — expect bit-exact |
| COM port unreachable / import errors | WSL vs Windows split; Win Python lacks the data stack | Raw UART tests on Windows COM8; data-stack tools in WSL |
| Report crashes on Windows | cp1252 can't encode `Δ`/`≥` | ASCII `dW`/`dS`/`>=` |

---

## Phase 7 Status: COMPLETE

Inference runs on the Basys 3, is provably correct (50/50 bit-exact vs simulation), and
demonstrably computes rather than replays. The end-to-end pipeline — Python feature build →
UART → hardware MLP → decoded prediction — is closed. The only optional remaining item is
demoing the Tkinter UI live against real team stats from WSL.
