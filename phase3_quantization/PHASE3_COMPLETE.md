# Phase 3 — Quantization: Complete

## Did Everything Work?

**Yes.** All 47 tests pass (14 Phase 1 + 14 Phase 2 + 19 Phase 3). The quantized
model matches the full-precision model within 0.2% win accuracy and 0.01 pts
spread MAE. All four hls4ml readiness checks pass. Artifacts are saved and ready
for Phase 4.

---

## What Was Built

Five Python modules that convert the float32 Keras model into a fixed-point
QKeras model, verify it is hls4ml-compatible, and run readiness checks.

| File | Role |
|---|---|
| `qkeras_model.py` | QKeras model definition — mirrors Phase 2 architecture |
| `quantize.py` | Entry point: weight transfer, fine-tuning, evaluation, hls4ml checks |
| `evaluate_quantized.py` | Side-by-side comparison utilities and report writer |
| `verify_hls_ready.py` | Five hls4ml compatibility checks before Phase 4 |
| `__init__.py` | Package marker |

---

## What It Produced

```
artifacts/model_quantized.keras     — QKeras model with fine-tuned fixed-point weights
artifacts/quantization_report.json  — full metrics, bit config, hls4ml readiness fields
```

---

## How to Re-Run It

```bash
source venv/bin/activate
python phase3_quantization/quantize.py   # weight transfer → fine-tune → evaluate → hls4ml checks
pytest tests/ -v                         # runs all 47 tests
```

---

## What Quantization Means Here

The Phase 2 model stores every weight as a 32-bit float. The FPGA (Basys 3)
can't do that efficiently — it maps each multiply-accumulate to a DSP block, and
DSP blocks work with integers. The goal of Phase 3 is to convert every weight and
activation to a fixed-point integer without losing meaningful accuracy.

**Fixed-point notation:** `<total_bits, integer_bits>`
- `<8,0>` = 8 bits, 0 integer bits → values in [-1, 1) in steps of 1/128
- `<8,4>` = 8 bits, 4 integer bits → values in [-8, 8) in steps of 1/16
- `<16,6>` = 16 bits, 6 integer bits → larger range, finer precision

Why this matters for Phase 4:
- Narrower bits → fewer DSPs per MAC → more parallelism for the same budget
- hls4ml reads these bit widths directly from the QKeras layer config to decide
  how to map each layer to FPGA primitives
- Too narrow → accuracy degrades. Too wide → wastes BRAM and DSPs.

---

## What Happened Step by Step

### 1. QKeras Model (`qkeras_model.py`)

A QKeras model was written to mirror the Phase 2 architecture exactly — same
layer names, same sizes, same output heads. The only differences:
- `Dense` → `QDense` with fixed-point kernel and bias quantizers
- `ReLU` → `QActivation(quantized_relu(...))` with fixed-point activation
- Dropout removed entirely — it is training-only and has no FPGA equivalent

```
Input (21 features)
    QDense(128, kernel=<8,0>, bias=<16,6>) → QActivation(relu <8,4>)
    QDense(64,  kernel=<8,0>, bias=<16,6>) → QActivation(relu <8,4>)
    QDense(32,  kernel=<8,0>, bias=<16,6>) → QActivation(relu <8,4>)
    /                       \
Dense(1, sigmoid)      Dense(1, linear)
   win output              spread output
```

Output heads are standard `Dense` (not quantized) — hls4ml handles output
layer precision separately in Phase 4.

**Why these bit widths:**
| Layer | Quantizer | Reason |
|---|---|---|
| Kernel | `<8,0>` | Inputs scaled [0,1]; weights near zero. 8 bits → ±1 range covers them. |
| Bias | `<16,6>` | Biases accumulate across inputs and can be larger. 6 integer bits = ±64 range. |
| Activation | `<8,4>` | ReLU output is non-negative; 4 integer bits covers the realistic post-activation range. |

**Total parameters: 13,218** — same as Phase 2 (quantization doesn't add parameters).

### 2. Weight Transfer

Weights from `artifacts/model_best.keras` were copied directly into the QKeras
model layer by layer. Only layers with weights were transferred — `QActivation`
layers have no weights, and Dropout doesn't exist in the QKeras model.

```
Layers transferred: dense_1, dense_2, dense_3, win, spread
```

After transfer, every layer had max weight delta = 0.000000 — the values are
numerically identical before any fine-tuning (float32 → float32 copy, no rounding yet).

### 3. Pre-Fine-Tuning Evaluation (Raw Quantization Impact)

Before any training, the model was evaluated with its quantizers active (weights
are rounded to fixed-point during the forward pass). This measures how much
the quantization rounding alone degrades the model.

```
Metric             Full Precision   Quantized   Delta
Win Accuracy               0.645       0.648   -0.004   ← actually better
Spread MAE                  9.74        9.75    +0.01
Mean Prob Delta               —        0.024
```

The quantized model was already *better* than fp32 before any fine-tuning.
This is unusual but possible — quantization noise can act like regularization
and improve generalization slightly. The accuracy drop was well within the 2%
tolerance, meaning the `<8,0>` kernel quantizer was a good fit for these weights.

### 4. Quantization-Aware Fine-Tuning

The QKeras model was fine-tuned for up to 30 epochs with the quantizers active
during the forward pass. This lets the optimizer adjust the floating-point weights
so that their quantized versions (the rounded integers) minimize the loss — instead
of just rounding the original weights and hoping for the best.

Fine-tuning config:
- Learning rate: 0.0001 (10× lower than Phase 2 — adjusting existing weights, not learning from scratch)
- Batch size: 32
- EarlyStopping: patience=10 on `val_win_accuracy`, mode='max'
- ModelCheckpoint: saves best `val_win_accuracy` to `artifacts/model_quantized.keras`

EarlyStopping triggered at epoch 11, restoring weights from epoch 1 (best
`val_win_accuracy = 0.6464`).

### 5. Post-Fine-Tuning Results

```
Metric             Full Precision   Quantized   Delta
Win Accuracy               0.645       0.646   -0.002   ← -0.2%, within 2% tolerance
Spread MAE                  9.74        9.75    +0.01   ← +0.01 pts, within 1.0 pt tolerance
Mean Prob Delta               —        0.018
```

Sample predictions (10 validation games):

```
idx   FP Win%    Q Win%   Delta   FP Sprd   Q Sprd
  0     75.4%     73.4%   -2.0%      +7.2     +6.3
 60     52.7%     53.6%   +0.9%      +1.1     +1.2
120     60.9%     59.2%   -1.6%      +3.5     +3.0
180     32.1%     31.4%   -0.7%      -4.5     -4.6
240     27.6%     25.6%   -2.1%      -6.8     -6.9
301     77.5%     76.2%   -1.3%      +8.4     +7.7
361     78.6%     77.6%   -1.0%      +8.7     +8.1
421     85.5%     84.8%   -0.7%     +11.5    +10.7
481     59.1%     58.2%   -0.9%      +2.8     +2.7
542     76.9%     72.9%   -4.0%      +8.3     +7.0
```

Max per-game win probability delta: 4.0%. All under the 10% per-game tolerance.
The predictions are directionally identical on all 10 sampled games.

### 6. hls4ml Readiness Checks

Five checks were run before Phase 4 to catch incompatibilities early:

```
CHECK 1 — Layer types:        PASS  (QDense, QActivation, Dense, InputLayer — all supported)
CHECK 2 — Activations:        PASS  (hidden layers all ReLU)
CHECK 3 — Bit widths:         PASS  (kernel=8, bias=16, activation=8 — all in [1,16])
CHECK 4 — No dropout:         PASS
CHECK 5 — Resource estimate:
  Total weights:         13,218
  Est. DSPs (unrolled):  13,218  (Basys 3 budget: 90)
  NOTE: Exceeds 90 DSPs at reuse_factor=1. Expected for this model size.
  Recommended starting point: reuse_factor = 147
```

Check 5 is informational — it never blocks Phase 4. The model's 13k weights
won't fit in 90 DSPs at reuse_factor=1 (fully unrolled), but hls4ml's
`reuse_factor` setting time-multiplexes the DSPs: with reuse_factor=147, each
DSP handles 147 MAC operations sequentially, using fewer DSPs at the cost of
more clock cycles per inference. The recommended reuse_factor of 147 is the
starting point to try in Phase 4.

---

## Bugs Encountered and Fixed

### 1. QKeras 0.9 + Keras 3 Eager Mode Incompatibility

**Symptom:** `numpy() is only available when eager execution is enabled` crash
inside QKeras quantizers during model build or forward pass.

**Root cause:** QKeras 0.9 calls `.numpy()` on tensors inside its quantizer
implementations. Keras 3 runs functions in graph mode by default — `.numpy()`
is not available in graph mode.

**Fix:** Add `tf.config.run_functions_eagerly(True)` at the top of every Phase 3
file **before** any imports of QKeras or Keras models. This forces all operations
to run eagerly, making `.numpy()` available everywhere.

```python
import tensorflow as tf
tf.config.run_functions_eagerly(True)  # QKeras 0.9 needs eager mode for .numpy() in quantizers
import keras
from qkeras import QDense, QActivation, ...
```

Note: `tf.config.experimental_run_functions_eagerly` does NOT apply to `tf.data`
functions (you'll see a UserWarning about this — it's harmless).

### 2. QKeras Model Load Broken in Keras 3

**Symptom:** `keras.models.load_model('artifacts/model_quantized.keras')` raises
`TypeError: Could not locate class 'QDense'`.

**What doesn't work:** Passing `custom_objects` — even with all QKeras classes
registered, loading then fails with `TrackedDict object is not callable` inside
`QActivation.call()`.

**Fix:** Never use `keras.models.load_model` on a QKeras `.keras` file.
Instead, build a fresh model and load only the weights:

```python
model = build_quantized_model(n_features=len(features))
compile_quantized_model(model)
model.load_weights('artifacts/model_quantized.keras')
```

This works because the architecture is always known (defined in `qkeras_model.py`),
so only the weight values need to be read from disk.

### 3. numpy float32 Not JSON Serializable

**Symptom:** `TypeError: Object of type float32 is not JSON serializable` when
calling `json.dump()` in `save_quantization_report()`.

**Root cause:** Keras prediction outputs are numpy `float32` arrays. Python's
`round()` preserves the numpy type; `json.dump` only serializes Python native
`float`.

**Fix:** Wrap every value in `float()` before rounding:
```python
'fp_win_accuracy': round(float(metrics['fp_win_acc']), 4),
```
And wrap booleans with `bool()` for the same reason.

---

## How Tests Were Run & What They Verified

```bash
pytest tests/ -v
```

**Result: 47/47 passed** (14 Phase 1 + 14 Phase 2 + 19 Phase 3).

Phase 3 tests:

| Test | What it checks |
|---|---|
| `test_features_json_has_21_features` | features.json still has exactly 21 — catches any drift |
| `test_qkeras_model_input_shape` | input shape is (None, 21) — n_features read from file, not hardcoded |
| `test_qkeras_model_output_shapes` | win shape (10,1), spread shape (10,1) on a batch |
| `test_win_probs_in_unit_interval` | sigmoid output in [0,1] — fixed-point overflow can push it outside |
| `test_spread_outputs_in_plausible_range` | spread in [-60, 60] |
| `test_dropout_absent_from_qkeras_model` | no dropout layer in inference model |
| `test_quantized_parameter_count` | params < 50,000 (actual: 13,218) |
| `test_weight_transfer_correctness` | max weight delta < 0.001 immediately after transfer |
| `test_accuracy_drop_within_tolerance` | fp_acc - q_acc <= 2% |
| `test_quantized_accuracy_above_floor` | q_acc >= 63% |
| `test_spread_mae_degradation` | q_mae - fp_mae <= 1.0 pt |
| `test_quantized_model_saved` | artifacts/model_quantized.keras exists |
| `test_quantization_report_saved` | artifacts/quantization_report.json exists |
| `test_quantization_report_ready_for_phase4` | report['ready_for_phase4'] == True |
| `test_deterministic_inference` | same input → identical output both times (no random ops) |
| `test_numerical_agreement_per_game` | per-game win prob delta < 0.10 for 20 val games |
| `test_qdense_kernel_quantizer_bits` | kernel quantizer is <8,0> (best-effort, skips if API unavailable) |
| `test_qdense_bias_quantizer_bits` | bias quantizer is <16,6> (best-effort) |
| `test_qactivation_bits` | activation quantizer mentions 8 bits (best-effort) |

---

## Key Design Decisions & Why

- **`<8,0>` kernel quantizer** — inputs are scaled [0,1] by MinMaxScaler, so weights
  near zero are sufficient. 8 bits gives 256 discrete values in [-1,1) — enough
  resolution for the learned weight distribution.

- **`<16,6>` bias quantizer** — biases accumulate across all inputs (128 inputs for
  dense_1, 64 for dense_2). They can be larger than weights and need more integer
  bits to avoid overflow. 16 bits total with 6 integer bits = range [-64, 64).

- **`<8,4>` activation quantizer** — post-ReLU activations are always non-negative.
  4 integer bits covers values up to 8.0, which is realistic after a ReLU with
  well-initialized weights. 8 total bits gives fine fractional resolution (1/16).

- **Output heads unquantized** — win (sigmoid) and spread (linear) outputs are left
  as float32. hls4ml applies its own output precision in Phase 4, and leaving them
  unquantized here lets Phase 4 choose the right output representation without
  interference.

- **Fine-tuning LR = 0.0001 (10× lower than Phase 2)** — we are adjusting weights
  that already have good values, not learning from scratch. A large learning rate
  would overwrite the learned weights; a small one nudges them to be more robust
  to quantization rounding.

- **`reuse_factor = 147` starting point for Phase 4** — with 13,218 weights and
  90 DSPs on Basys 3, full unrolling is impossible. hls4ml's reuse_factor tells
  it how many MACs to share per DSP. 147 ≈ ceil(13218/90) is the minimum reuse
  that fits in 90 DSPs. Phase 4 will tune this to balance latency vs resource use.

---

## Artifacts Produced

| File | Description |
|---|---|
| `artifacts/model_quantized.keras` | Fine-tuned QKeras model weights — input to Phase 4 hls4ml conversion |
| `artifacts/quantization_report.json` | Bit config, accuracy metrics, hls4ml readiness, recommended reuse_factor |

The `model_best.keras` (Phase 2) remains the source of truth for weight transfer.
`model_quantized.keras` is always regenerated from `model_best.keras` by running
`quantize.py` — never edit its weights directly.
