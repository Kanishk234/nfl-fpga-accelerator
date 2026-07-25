# Phase 7 (conifer) — Deployment Host + Board Bring-Up

> **Status: COMPLETE** — the GBDT accelerator runs on the Basys 3 and its output
> is **bit-exact against simulation on 100/100 games**, zero timeouts, median
> 6.9 ms round-trip. NACK, SOF-collision and recovery paths verified on silicon;
> a feature sweep proves the FPGA is evaluating trees, not replaying a table.
> Host suite: 30 passed + 3 board tests. Verified 2026-07-25 on COM8.

This is the GBDT analog of `phase7_deploy/` (MLP board deployment). Everything
new lives here per the `_conifer` sibling-folder convention; the one exception
is `phase7_deploy/ui/index.html`, which is now **shared** by both UIs
(see [One page, two bitstreams](#one-page-two-bitstreams)).

---

## The Protocol (different from the MLP — not interchangeable)

The two bitstreams speak different languages. Pointing the MLP host at the GBDT
board (or vice versa) does not degrade gracefully; it desyncs.

| | MLP (`phase7_deploy`) | GBDT (this folder) |
|---|---|---|
| Request | 23 B — `0xAA` + 21 INT8 + XOR | **65 B** — `0xAA` + **63** raw bytes + XOR |
| Feature encoding | `scaler.transform` → `round(v·255)` | **no scaler** — `round(raw·4096)`, int24 LSB-first |
| Response | 4 B — `0x55` + win_u8 + spread_i8 + status | **8 B** — `0x55` + win[3B] + spread[3B] + status |
| Result precision | quantized to a byte | full `ap_fixed<24,12>` word |
| Comparison to sim | ±2 counts of slack | **bit-exact, zero tolerance** |

The GBDT uses raw, unscaled features because trees split on raw thresholds —
`home_elo ≈ 1522` cannot fit in a byte. Sending full-width values costs 42 extra
bytes per request and buys something valuable: the board's output is directly,
exactly comparable to the phase-6 golden, with nothing to hide behind.

Protocol source of truth: `phase5_fpga_conifer/hdl/uart_framing_conifer.v`.

---

## What Was Built

```
phase7_deploy_conifer/
├── inference/
│   ├── fpga_client_gbdt.py       GBDTClient — frame, checksum, send, decode 8B
│   └── feature_builder_gbdt.py   raw game -> 63 wire bytes (subclasses the MLP builder)
├── export_catalog_gbdt.py        WSL: builds BOTH json artifacts below
├── games_catalog_gbdt.json       6,427 games x 63 precomputed bytes (2.1 MB, generated)
├── validation_vectors.json       the 100 phase-6 vectors + expected words + team names
├── board/verify_uart_gbdt.py     one-packet smoke test, self-checking vs golden
├── validation/golden_vector_test_gbdt.py   100-game board regression, zero tolerance
└── ui/webapp_gbdt.py             local web UI (stdlib http.server + pyserial)

tests/test_phase7_conifer.py      30 host tests + 3 FPGA_PORT-gated board tests
```

### Reuse rather than copies

Three things are inherited from the MLP flow instead of duplicated, following
the precedent set in phase 5 (where `uart_rx.v`/`uart_tx.v` are *sourced* from
`phase5_fpga/hdl/`, not copied):

- **`GBDTFeatureBuilder` subclasses `FeatureBuilder`.** Game lookup, matchup
  construction, and `from_current_week` are model-independent — they just
  assemble 21 features in the locked `features.json` order. Only the encoding
  differs, so only the encoding is overridden.
- **`GameCatalog` is imported as-is** from `phase7_deploy/inference/`; it is a
  generic read-only view over the catalog JSON and needed no changes.
- **`index.html` is shared**, made model-driven — see below.

`scaler.pkl` is still loaded by the inherited `__init__` but is **never applied**
here. The GBDT was trained on raw features; scaling would be wrong, not merely
redundant.

---

## One page, two bitstreams

Duplicating ~280 lines of near-identical HTML for a UI that differs only in a
few labels would guarantee the two copies drift. Instead, `index.html` now reads
everything build-specific from a `model` block in `/api/bootstrap`:

```python
MODEL = {                                   # webapp_gbdt.py
    'name': 'GBDT',
    'sub': '2-stage XGBoost · conifer',
    'steps': ['Encode 63B', 'TX →', 'FPGA GBDT ×2', '← RX 8B'],
    'raw_denom': 4096,        # ap_fixed<24,12>, vs 256 for the MLP
    'spread_decimals': 2,     # full-width result, vs 0 whole points
}
```

The page renders a **model badge in the header** (`GBDT · 2-stage XGBoost`,
accent-coloured so it is unmissable), sets the document title, labels the
pipeline stepper, formats the spread to the right precision, and shows the raw
win word over the right denominator. The JS defaults describe the MLP, so the
MLP app is unchanged in behaviour and would still work even if its server sent
no `model` block at all.

**Verified headless:** both servers were started and `GET /` returned
**byte-identical** 13,914-byte pages (`identical=True`), while
`/api/bootstrap` correctly reported `MLP · 4-layer dense · hls4ml`
(`raw_denom` 256) on :8713 and `GBDT · 2-stage XGBoost · conifer`
(`raw_denom` 4096) on :8714. `tests/test_phase7.py` still passes 22/22, so the
shared-page change did not regress the MLP UI.

Default HTTP port is **8714** (MLP is 8713) so both can run simultaneously —
though only one process may hold the COM port, and only one bitstream is on the
board at a time.

---

## Challenges Encountered

### 1. The float32 cast — a second 1-ulp encoding bug, caught offline

**Symptom:** the first version of the host encoder disagreed with
`chain_golden/tb_inputs.mem` on **48 of 100 games**, always by exactly 1 in the
LSB (`home_elo`: host `5f27a9`, golden `5f27aa`).

**Root cause:** `make_chain_golden.py` quantizes
`va[FEAT].values.astype('float32')` — the features are cast to **float32**
before `round(x·4096)`. The host read the same values from the parquet as
**float64**. For `home_elo ≈ 1522.48`, float32 carries only ~7 significant
digits, so the two paths straddle the rounding boundary and land one step apart.

**Fix:** `GBDTFeatureBuilder.quantize()` casts to float32 first, and uses
`np.round` (half-to-even) rather than Python's `round`, exactly matching the
golden. Pinned by `test_host_encoding_matches_chain_golden`.

**Why it matters:** this is the *same class* of bug as phase 6's
round-vs-truncate 1-ulp skew, from the opposite direction — and it would have
produced the identical symptom on hardware: a board that is *mostly* bit-exact,
failing on about half the games for no visible reason, with no way to tell a
host bug from an RTL bug. Catching it required deciding up front that the host
encoder must be provable against the simulated vectors **without a board**.
float32 is not an arbitrary choice, either: it is what the models were trained
on, so it is the canonical value a feature *has*.

### 2. Vector 0's first feature byte is `0xAA`

While checking the encoding, the very first byte of the very first validation
game turned out to be `0xAA` — the request start-of-frame marker. Phase 6's
SOF-collision test (all 63 feature bytes = `0xAA`, framing must treat them as
data) was written as a defensive edge case; it is in fact exercised by the
**first real game anyone will run**. `verify_uart_gbdt.py` calls this out in its
output so the coincidence isn't mistaken for corruption.

### 3. Validation had to run on Windows, where there is no pandas

The board's COM port is on Windows; `pandas` and the parquet are in the WSL
venv. The MLP solved this for the UI with a precomputed catalog, but its
`golden_vector_test.py` still parses three phase-6 files at runtime and
re-derives which games to use.

**Approach taken:** `export_catalog_gbdt.py` emits a second artifact,
`validation_vectors.json`, bundling for each of the 100 vectors the input bytes,
the expected output words, the team names, and the XSIM result. The board tests
then need only `pyserial` + one JSON file. The exporter **re-encodes the inputs
from the parquet and asserts they equal `tb_inputs.mem`**, so the bundle cannot
silently drift from what was simulated, and it **cross-checks `sim_results.csv`
against `golden_fixed.mem`** — holding phase 6's "XSIM is bit-exact" claim to
account every time the bundle is rebuilt. Both assertions pass
(`100 vectors (XSIM cross-checked)`).

### 4. Zero tolerance, deliberately

The MLP's board regression allows ±2 win counts. Copying that here would be a
mistake: the GBDT returns full 24-bit words, and the board runs the identical
RTL that XSIM already proved bit-exact against a golden that models the chain
bit-for-bit. The only correct expected difference is **zero**, so
`golden_vector_test_gbdt.py` has no tolerance band at all and says so in its
banner. A tolerance band here would have swallowed challenge #1.

### 5. No `program_board.py`

The MLP's equivalent hardcodes a Windows Vivado path that does not exist on this
machine, and phase 7 (MLP) ended up programming through the Vivado IDE Hardware
Manager anyway. Rather than reproduce a script that is known not to work here,
the bring-up steps below document the GUI route and the exact bitstream path.

---

### 6. First contact returned nothing — and that was the useful signal

The first smoke test on a connected board timed out with **0 bytes**. Rather
than guess, a probe sent (a) a correct 65-byte GBDT packet, (b) a 23-byte MLP
packet, (c) 64 bytes of noise, and (d) listened passively for 2 s. **All four
were silent.** That distinguishes the cases: a board running the *MLP* bitstream
would have answered the 65-byte packet with a NACK (`55 00 00 01`), because
`0xAA` + 21 bytes + a mismatched 23rd byte is a well-formed-but-wrong MLP frame.
Total silence means nothing is driving UART TX at all — an unprogrammed board.

It was. The bitstream is volatile and the board had been power-cycled back to the
Digilent factory demo. Worth recording because "no response" is the same symptom
as the UART pin ambiguity that cost the MLP track real debugging time (AUDIT;
`PHASE7_COMPLETE.md` issue 1) — the four-probe pattern separates *unprogrammed*
from *miswired* from *wrong bitstream* in one shot, without touching the XDC.

---

## Board Bring-Up

**The bitstream already exists** (built in phase 5, Jul 19):

```
C:\nfl_gbdt_build\nfl_gbdt_accelerator.runs\impl_1\top_gbdt.bit
```

The MLP bitstream is untouched at `C:\nfl_fpga_build\...\impl_1\top.bit` — two
separate build directories, so flashing one never overwrote the other. The
bitstream is volatile: every power cycle reloads the Digilent factory demo, so
reprogram each session.

```powershell
# 1. Program: Vivado IDE -> Open Hardware Manager -> Auto Connect ->
#    Program device -> C:\nfl_gbdt_build\nfl_gbdt_accelerator.runs\impl_1\top_gbdt.bit
#    (the 7-seg counting demo must STOP = our bitstream is loaded)

# 2. Smoke test — one real game, self-checking against the golden
python phase7_deploy_conifer\board\verify_uart_gbdt.py COM8

# 3. Full 100-game bit-exact regression
python phase7_deploy_conifer\validation\golden_vector_test_gbdt.py COM8

# 4. Web UI
python phase7_deploy_conifer\ui\webapp_gbdt.py        # http://127.0.0.1:8714
```

```bash
# Rebuild the JSON artifacts (WSL) — only needed if the dataset changes
source venv/bin/activate
python phase7_deploy_conifer/export_catalog_gbdt.py

# Tests: 30 pass without a board; add the 3 board tests with FPGA_PORT set
pytest tests/test_phase7_conifer.py -v
FPGA_PORT=COM8 pytest tests/test_phase7_conifer.py -v
```

### Sign-off checklist — all green (2026-07-25, COM8)

| # | Gate | Expected | Result |
|---|---|---|---|
| 1 | Board enumerates | a `USB Serial` port appears | ✅ COM8, FT2232 `VID:PID=0403:6010` |
| 2 | Smoke test | status OK **and** bit-exact | ✅ TB vs DAL 77.9%, +9.705, exact |
| 3 | 100-game regression | 100/100 bit-exact, 0 timeouts | ✅ **100/100**, 0 timeouts, 0 bad status |
| 4 | NACK path | corrupted checksum → `0x01` | ✅ `55 00 00 00 00 00 00 01` |
| 5 | Latency | ~7–16 ms | ✅ median **6.9 ms**, min 6.6 |
| 6 | UI | GBDT badge, end-to-end run | ✅ badge correct, `/api/infer` → 3189/4096 |

---

## Board Results

### Smoke test — first contact

```
Using vector 0: TB (H) vs DAL (A), 2021 week 1
  first feature byte = 0xAA  <- same as SOF, exercises the collision path
  Win probability: 77.9%   (raw 3189/4096, golden 3189)
  Point spread:    +9.705  (raw 0x009B47, golden 0x009B47)
  Status: OK      Latency: 6.9 ms      Bit-exact: YES
```

The full chain — UART RX → framing → checksum → controller → conifer_win →
sigmoid ROM → conifer_spread → +vegas → UART TX — works in silicon, and the very
first packet exercised the `0xAA` SOF-collision path for free.

### 100-game regression — bit-exact, no tolerance

```
games        : 100
bit-exact    : 100/100
timeouts     : 0
bad status   : 0
latency (ms) : min 6.6   median 6.9   max 8.2
```

Every one of the 100 phase-6 vectors came back as the **exact 24-bit words**
XSIM produced. This is a materially stronger result than the MLP track's
50/50 pass: that comparison allowed ±2 counts because the MLP returns quantized
bytes, whereas here the full `ap_fixed<24,12>` words must match with **zero**
slack. Python C++-emulation golden → XSIM → silicon agree bit-for-bit across
three independent implementations of the same arithmetic.

### Error paths and recovery

| Test | Sent | Response | Verdict |
|---|---|---|---|
| NACK | valid packet, checksum XOR `0xFF` | `55 00 00 00 00 00 00 01` | status `0x01` ✅ |
| SOF-collision | all 63 feature bytes = `0xAA` | `55 a1 03 00 e5 a8 aa 00` | status `0x00`, treated as data ✅ |
| Recovery | good packet right after both | 77.9% / +9.705, bit-exact | FSM resynced ✅ |

The SOF-collision response is worth a second look: the spread word's MSB is
itself `0xaa`. Garbage in, deterministic garbage out — and the framing FSM never
mistook any of it for a new frame.

### Proof of computation — the FPGA is evaluating trees

Sweeping `elo_diff` from −300 to +300 with the other 20 features pinned. None of
these inputs exist in any dataset, so a lookup table cannot produce them:

```
elo_diff   -300   -200   -100    -50     +0    +50   +100   +150   +200   +300
win%      75.0%  75.0%  75.6%  76.5%  77.6%  78.1%  81.4%  82.8%  84.1%  84.1%
spread    +9.78  +9.78  +9.82  +9.82  +9.80  +9.70  +9.76 +10.16 +10.16 +10.16
```

Two things this shows that the MLP's equivalent sweep could not:

1. **It is a staircase, not a ramp** — 8 distinct levels across 13 points, with
   flat plateaus at both extremes. That is exactly what an ensemble of depth-2
   decision trees looks like: output changes only when a split threshold is
   crossed. The MLP's sweep moved continuously; this one moves in steps, and the
   step structure *is* the tree structure, visible from outside the chip.
2. **It is monotonic in `elo_diff`** (verified, no inversions). The win head was
   trained with monotone constraints (`phase2_gbdt`), and that training-time
   constraint survives XGBoost → conifer → HLS → fixed-point → silicon intact.

### Latency — 25 shots

```
n=25  min 6.78  mean 8.01  median 7.69  max 13.07  ms
```

| Component | Time | Note |
|---|---:|---|
| UART line time | **6.34 ms** | 73 bytes × 10 bits ÷ 115200 — now the dominant term |
| USB bridge + host | ~0.4–1.7 ms | FTDI latency timer still at 1 ms from the MLP bring-up |
| FPGA compute | **0.00022 ms** | 22 cycles @ 100 MHz |

The ranking **flips versus the MLP**. There, compute was negligible and a 16 ms
USB-bridge default dominated (fixed 11.5 → 3.4 ms). Here compute is even more
negligible — 22 cycles against the MLP's 1,494, a **68× shorter** inference —
and the protocol's own line time is the floor. The measured 6.9 ms median sits
just above the 6.34 ms theoretical minimum, confirming the FTDI timer is still
set to 1 ms (it is a persistent registry value keyed to the board's serial). If a
future run shows ~16–20 ms, reset it: Device Manager → COM8 → Port Settings →
Advanced → *Latency Timer* → 1.

Cutting the remaining time means raising the baud rate (`CLKS_PER_BIT` 868 → 100
for 1 Mbaud) and re-synthesizing — the same documented, not-taken lever as the
MLP track. Paying 6.3 ms of line time is the deliberate price of full-width,
bit-exact-comparable results; the alternative was quantizing to bytes and giving
up the zero-tolerance gate.

### UI on real hardware

`webapp_gbdt.py` on :8714 auto-selected COM8, reported the badge
`GBDT · 2-stage XGBoost · conifer`, and `POST /api/infer` returned
`raw_win 3189` / spread `+9.7048` / OK in 7.1 ms — the same words as the CLI and
the golden. TB did win that game, so the UI shows ✓ correct.

---

## Test Results (host side, no board)

```
tests/test_phase7_conifer.py — 30 passed, 3 skipped (board-gated) in 4.20s
tests/test_phase7.py         — 22 passed, 4 skipped   (MLP, unaffected)
```

Coverage: fixed-point conversion incl. two's complement and range rejection,
LSB-first packing, XOR checksum (incl. the `0xAA`-collision case), full
request/response handling against a `FakeSerial` (65-byte framing, decode, NACK,
watchdog, bad SOF, short read), bundle/catalog integrity, XSIM-vs-golden
agreement, the shared page being model-driven, and the encoding-matches-sim gate.

---

## Known Gaps

| Gap | Assessment |
|---|---|
| Board tests are manual, not in CI | They need hardware. `FPGA_PORT=COM8 pytest tests/test_phase7_conifer.py` runs them when the board is attached. |
| No Tkinter desktop app | The web UI supersedes it; the MLP's Tkinter app remains for the MLP. |
| No `logger.py` / inference audit log | The MLP's writes a CSV the web UI never used. Not reproduced rather than shipped dead. |
| `games_catalog_gbdt.json` is 2.1 MB in git | Same tradeoff the MLP made (1.3 MB): it is regenerable in one command, but Windows cannot regenerate it, so it is committed. |
| `from_current_week()` inherited untested | Works via the base class, but upcoming-game features are approximations; not part of the bit-exact story. |

---

## Phase 7 (conifer) Status: COMPLETE

The two-stage GBDT accelerator runs on the Basys 3 and reproduces simulation
**bit-exactly on 100/100 games with zero tolerance** — a stricter correctness
gate than the MLP track ever achieved, and one the MLP could not have met by
construction (it returns quantized bytes). Four independent implementations of
the same arithmetic agree bit-for-bit: the XGBoost/C++ emulation golden, the
conifer HLS cosim, XSIM on the synthesized netlists, and the silicon.

The end-to-end pipeline is closed: `games.parquet` → float32 quantization →
65-byte UART → two conifer IPs chained through a sigmoid ROM → 8-byte response →
decoded prediction in a browser. 22 cycles of compute, 12,095 LUTs, 0 DSPs.

### GBDT vs MLP, deployed

| | GBDT | MLP |
|---|---|---|
| Board-vs-sim agreement | **100/100 bit-exact, no tolerance** | 50/50 within ±2 counts |
| Inference latency | **22 cycles (0.22 µs)** | 1,494 cycles |
| Round-trip | 6.9 ms median (line-time bound) | 3.4 ms (23-byte packet) |
| Slice LUTs | **12,095 (58%)** | 17,896 (86%) |
| DSPs | **0** | 18 |
| Val accuracy / spread MAE | 65.38% / 9.77 | ~63% / ~9.0 |

The GBDT is more accurate, 68× faster to compute, uses a third fewer LUTs and no
DSPs at all. Its only loss is wall-clock round-trip, and that is a protocol
choice (full-width results) rather than a property of the model — recoverable at
any time by raising the baud rate.

**Next:** merge the `_conifer` folders into the main tree (see the conifer
phase-naming convention) now that board deploy is done.
