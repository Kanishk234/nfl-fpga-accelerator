# Build & Run

Everything needed to reproduce this project, from a clean checkout to a bit-exact run on
hardware. The [README](../README.md) is the overview; this is the operator's manual.

**Two independent bitstreams.** The MLP and the GBDT are separate builds with separate
UART protocols. They are *not* interchangeable — the model you can run is whichever one you
last flashed to the board.

---

## 1. Software path (no hardware required)

This half is fully reproducible on any machine with Python 3.12.

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

python phase1_data/pipeline.py               # nflreadpy → data/processed/games.parquet
python mlp/phase2_model/train.py             # → artifacts/mlp/model_best.keras
python mlp/phase3_quantization/quantize.py   # → artifacts/mlp/model_quantized.keras
python gbdt/phase2_train/train_gbdt.py       # → artifacts/gbdt/*.json

pytest tests/ -v
```

### What is committed vs. regenerated

| Artifact | Status |
|---|---|
| `data/processed/games.parquet` | Regenerable — `phase1_data/pipeline.py`, needs internet |
| `artifacts/features.json` | Committed, frozen. 21 features, order is permanent. |
| `artifacts/scaler.pkl` | Committed, frozen. Never refit — see below. |
| `artifacts/mlp/model_best.keras` | Committed. Source of truth for phases 3–7. |
| `artifacts/gbdt/*.json` | Committed. Source of truth for the GBDT track. |

Retraining overwrites the model files but must never overwrite `scaler.pkl` or
`features.json`. The scaler has to be byte-identical at training time and at inference time
forever, and the FPGA reads feature 0 from byte 0 — reordering `features.json` silently
corrupts every prediction without raising an error anywhere.

The GBDT deliberately does **not** use `scaler.pkl`. Trees split on raw thresholds, so
scaling would only add a lossy transform between the model and the hardware.

### Environment

- **OS:** WSL Ubuntu. The project was moved from Windows to WSL; all Python commands run
  inside WSL. Vivado runs on the Windows side.
- **Python:** 3.12.3 via WSL system Python (`python3.12-venv` from apt).
- **The venv is Linux-native.** Do not copy a Windows venv into WSL — TensorFlow will
  import and then fail at load time in ways that look like model corruption.
- **Key versions:** TensorFlow 2.21.0, Keras 3.14.1, qkeras 0.9.0.

---

## 2. Hardware path

**Requirements:** a Basys 3 board (Artix-7 XC7A35T), Vivado / Vitis HLS 2025.2 (free
WebPACK edition is sufficient), and a USB cable.

> This is not clone-and-go. You synthesize the bitstream yourself and you will need to fix
> a few absolute paths for your machine.

### 2.1 Synthesize

Edit the absolute paths in `*/phase5_fpga/scripts/*.tcl` first — they are hardcoded to the
original build machine.

```bash
# GBDT track
vivado -mode batch -source gbdt/phase5_fpga/scripts/create_project.tcl
vivado -mode batch -source gbdt/phase5_fpga/scripts/run_synth.tcl

# MLP track
vivado -mode batch -source mlp/phase5_fpga/scripts/create_project.tcl
vivado -mode batch -source mlp/phase5_fpga/scripts/run_synth.tcl
```

Produces `top_gbdt.bit` and `top.bit` respectively.

> **RTL cosimulation is a mandatory build gate, not an optional check.** The MLP's first IP
> passed C-simulation and a 50-game regression and then deadlocked on real silicon. C-sim
> substituted replay FIFOs that do not exist in the bitstream. Never promote an IP to
> synthesis on C-sim results alone. See [POST_AUDIT_REMEDIATION.md](POST_AUDIT_REMEDIATION.md).

> **Ignore the HLS resource estimate.** It has been wrong by 14× and 6.5× on this project.
> The GBDT's csynth estimated 96,878 + 33,369 LUTs, implying neither stage could ever fit
> on the chip; real Vivado synthesis came in at 6,758 + 5,111. Gate your fit decisions on
> post-route Vivado numbers against the 20,800-LUT budget, never on csynth.

### 2.2 Rebuild host-side catalogs

Run once, in WSL/Linux:

```bash
python mlp/phase7_deploy/export_catalog.py
python gbdt/phase7_deploy/export_catalog_gbdt.py
```

### 2.3 Program and verify

Flash the `.bit` via Vivado Hardware Manager (or `mlp/phase7_deploy/board/program_board.py`),
then, substituting your serial port for `COM8`:

```bash
# GBDT
python gbdt/phase7_deploy/board/verify_uart_gbdt.py COM8               # smoke test
python gbdt/phase7_deploy/validation/golden_vector_test_gbdt.py COM8   # 100-game bit-exact
python gbdt/phase7_deploy/ui/webapp_gbdt.py                            # → 127.0.0.1:8714

# MLP
python mlp/phase7_deploy/board/verify_uart.py COM8
python mlp/phase7_deploy/validation/golden_vector_test.py COM8
python mlp/phase7_deploy/ui/webapp.py                                  # → 127.0.0.1:8713
```

The bitstream is volatile — reprogram after every power cycle.

Board tests require physical hardware and cannot run in CI. They are gated behind the
`FPGA_PORT` environment variable so the rest of the suite stays green without a board
attached.

---

## 3. UART protocols

115200 baud, 8N1. The two bitstreams speak different protocols.

### MLP — 23 bytes out, 4 bytes in

| Direction | Layout |
|---|---|
| Host → FPGA | `0xAA` + 21 × INT8 scaled features + XOR checksum |
| FPGA → Host | `0x55` + `win_u8` + `spread_i8` + `status` |

Features are MinMax-scaled to [0,1], which drops straight into an unsigned byte.

### GBDT — 65 bytes out, 8 bytes in

| Direction | Layout |
|---|---|
| Host → FPGA | `0xAA` + 63 bytes of raw `ap_fixed<24,12>` + XOR checksum |
| FPGA → Host | `0x55` + `win[3B]` + `spread[3B]` + `status` |

The GBDT sends unscaled features because trees split on raw thresholds and
`home_elo ≈ 1522` does not fit in a byte — so each of the 21 values crosses as a full
24-bit word.

Returning full-width results costs 42 extra bytes per request and buys the thing that
makes the verification meaningful: the board's output is *exactly* comparable to
simulation, with no tolerance band to hide behind. The MLP's byte-quantized protocol
requires a ±2-count tolerance; the GBDT's does not.

### Encoding gotcha

The host encoder must cast features to **float32 before encoding**, matching what the
golden generator does. Reading them as float64 shifts the LSB on roughly half of all games.
This was caught pre-hardware only because the verification demanded exactness; with a
tolerance band it would have shipped, and on the board it would have presented as a
board that is *mostly* bit-exact and fails half the games for no visible reason.

---

## 4. Performance tuning

**Set the FTDI latency timer to 1 ms.** Device Manager → the board's COM port → Port
Settings → Advanced → Latency Timer, change 16 to 1.

That single driver setting took the MLP round-trip from ~11.5 ms to ~3.4 ms with no HDL
change at all. Profile before optimizing: the obvious suspect, baud rate, accounted for
only about 20% of the latency.

The GBDT's 6.9 ms round-trip is line-time bound — 6.34 ms of it is the 65-byte packet on
the wire at 115200 baud. Its actual compute is 22 cycles.

---

## 5. HDL layout

```
gbdt/phase5_fpga/hdl/top_gbdt.v          mlp/phase5_fpga/hdl/top.v
├── uart_rx.v / uart_tx.v  ← both tracks source these from mlp/phase5_fpga/hdl
├── uart_framing_conifer.v   65B/8B       ├── uart_framing.v    23B/4B
├── gbdt_controller.v        chains IPs   ├── mlp_controller.v  AXI-Stream beat
├── sigmoid_rom.v            1024 × 12    └── myproject         hls4ml io_stream IP
└── conifer_win + conifer_spread
```

### Cross-track references are deliberate

The GBDT sources `uart_rx.v`, `uart_tx.v` and `basys3.xdc` from `mlp/phase5_fpga/`, imports
`GameCatalog` from `mlp/phase7_deploy/`, subclasses the MLP's `FeatureBuilder`, and both web
UIs serve the same `mlp/phase7_deploy/ui/index.html` — which is model-driven, reading its
badge, pipeline labels and number formats from the `model` block in `/api/bootstrap`.

Single source of truth. Do not fork these files to "decouple" the tracks.

### The sigmoid ROM has no Python counterpart

`gbdt/phase6_sim/make_chain_golden.py` *is* the specification for the hardware sigmoid. It
emits the `sigmoid_lut.mem` that `sigmoid_rom.v` loads via `$readmemh`. One artifact
generates both the golden values and the ROM contents, so the model and the hardware
cannot drift apart.

---

## 6. Data integrity rules

These are correctness constraints, not style preferences. Violating any of them produces a
model that scores well in validation and is worthless.

- **Temporal splits only.** Train ≤ 2020, validate 2021–2022, test 2023–2024. Never a
  random split — a random split leaks future games into training and inflates accuracy.
- **Every rolling stat goes through `.shift(1)`,** so game *N*'s features are computed from
  games 1..*N*−1 only and never include its own result.
- **Elo features are pre-game elo,** not post-game.
- **Out-of-fold stacking** on the GBDT, so the spread head never trains on leaked win
  probabilities from the same rows.
- Feature decisions are validated across multiple seeds. Single-run deltas on ~540
  validation games are noise.

---

## 7. Troubleshooting

| Symptom | Cause |
|---|---|
| Board returns nothing | Bitstream lost to a power cycle — reprogram. |
| Predictions are wrong but well-formed | Wrong bitstream flashed for the client you're running. The protocols differ. |
| Round-trip ~11 ms instead of ~3.4 ms | FTDI latency timer still at 16 ms — see §4. |
| Board disagrees with sim on ~half of games | float64/float32 encoder mismatch — see §3. |
| Board tests skipped in pytest | `FPGA_PORT` is unset. Expected without hardware. |
| TensorFlow imports then dies | Windows venv copied into WSL. Rebuild it natively. |
| csynth says the design cannot fit | It is probably wrong by an order of magnitude. Run real synthesis. |

---

## Deeper reading

Per-phase write-ups — what was built, what broke, and the numbers that closed it:

| Phase | MLP | GBDT |
|---|---|---|
| 1 · Data | [PHASE1_COMPLETE.md](../phase1_data/PHASE1_COMPLETE.md) | *(shared)* |
| 2 · Train | [PHASE2_COMPLETE.md](../mlp/phase2_model/PHASE2_COMPLETE.md) | [PHASE2_GBDT_COMPLETE.md](../gbdt/phase2_train/PHASE2_GBDT_COMPLETE.md) |
| 3 · Quantize | [PHASE3_COMPLETE.md](../mlp/phase3_quantization/PHASE3_COMPLETE.md) | *(n/a — trees need no scaler or quantization stage)* |
| 4 · HLS | [PHASE4_COMPLETE.md](../mlp/phase4_hls/PHASE4_COMPLETE.md) | [PHASE4_CONIFER_COMPLETE.md](../gbdt/phase4_hls/PHASE4_CONIFER_COMPLETE.md) |
| 5 · FPGA | [PHASE5_COMPLETE.md](../mlp/phase5_fpga/PHASE5_COMPLETE.md) | [PHASE5_CONIFER_COMPLETE.md](../gbdt/phase5_fpga/PHASE5_CONIFER_COMPLETE.md) |
| 6 · Sim | [PHASE6_COMPLETE.md](../mlp/phase6_sim/PHASE6_COMPLETE.md) | [PHASE6_CONIFER_COMPLETE.md](../gbdt/phase6_sim/PHASE6_CONIFER_COMPLETE.md) |
| 7 · Deploy | [PHASE7_COMPLETE.md](../mlp/phase7_deploy/PHASE7_COMPLETE.md) | [PHASE7_CONIFER_COMPLETE.md](../gbdt/phase7_deploy/PHASE7_CONIFER_COMPLETE.md) |

Incident reports: [AUDIT_REPORT.md](AUDIT_REPORT.md) · [POST_AUDIT_REMEDIATION.md](POST_AUDIT_REMEDIATION.md)
