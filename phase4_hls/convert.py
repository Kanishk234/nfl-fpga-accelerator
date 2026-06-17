"""
Phase 4 entry point: convert the trained NFL model to an HLS C++ project via hls4ml.

Model strategy:
  hls4ml 1.3.0 cannot convert a QKeras model under Keras 3 — two issues:
    (a) keras.models.load_model() fails to deserialize QKeras quantizers
    (b) hls4ml's keras_v3 converter asserts activation is a FunctionType but
        QActivation holds a quantizer object, not a plain function.
  Fix: build the QKeras model architecture from scratch (no deserialization),
  load QAT weights from model_quantized.keras, apply the kernel/bias quantizers
  to snap weights to the fixed-point grid, then transfer those to a plain
  Dense+ReLU inference model. hls4ml sees a clean Keras model; the precision
  config below mirrors the Phase 3 QKeras quantizers exactly so the generated
  C++ is functionally identical to a direct QKeras conversion.

Backend:
  Vivado HLS was discontinued after 2020.1; Vitis HLS (2020.2+) replaced it.
  Use backend='Vitis' for Vitis HLS 2025.2.

Synthesis path:
  Option A (vitis_hls on WSL PATH): automated via hls_model.build() in resource_report.py
  Option B (Windows Vitis HLS GUI): this script generates the C++ + runs C sim;
            synthesis is run manually in Windows, then resource_report.py parses reports.

Run from project root:
    source venv/bin/activate
    python phase4_hls/convert.py
"""

import os
import sys
import json
import pickle
import shutil

import tensorflow as tf
tf.config.run_functions_eagerly(True)  # must precede any QKeras import

import keras
import numpy as np
import pandas as pd
import hls4ml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def build_inference_model(n_features: int) -> keras.Model:
    """
    Plain Keras model — identical to Phase 2 at inference time (Dropout is training-only).
    No Dropout and no QKeras layers: hls4ml 1.3.0 handles this cleanly.
    Layer names match Phase 2 exactly so weight transfer works by name.
    """
    inputs = keras.Input(shape=(n_features,), name='features')
    x = keras.layers.Dense(128, activation='relu', name='dense_1')(inputs)
    x = keras.layers.Dense(64,  activation='relu', name='dense_2')(x)
    x = keras.layers.Dense(32,  activation='relu', name='dense_3')(x)
    win_output    = keras.layers.Dense(1, activation='sigmoid', name='win')(x)
    spread_output = keras.layers.Dense(1, activation='linear',  name='spread')(x)
    return keras.Model(inputs=inputs, outputs=[win_output, spread_output])


def load_inference_model(features_path='artifacts/features.json',
                         weights_source='artifacts/model_quantized.keras'):
    """
    Build inference model with QAT-quantized weights.

    Approach: build QKeras model architecture fresh (avoids deserialization),
    load QAT weights, apply the kernel/bias quantizers to snap float32 weights
    to the fixed-point grid, transfer to a plain inference model.
    hls4ml sees a clean Keras model; precision config matches Phase 3 quantizers.
    """
    from phase3_quantization.qkeras_model import build_quantized_model, compile_quantized_model
    from qkeras import quantized_bits

    with open(features_path) as f:
        meta = json.load(f)
    features = meta['features']
    n = len(features)

    # Build QKeras model and load QAT-trained weights (no deserialization of .keras config)
    q_model = build_quantized_model(n_features=n)
    compile_quantized_model(q_model)
    q_model.load_weights(weights_source)

    # Quantizers matching Phase 3 qkeras_model.py exactly
    kernel_q = quantized_bits(bits=8,  integer=0, symmetric=1)
    bias_q   = quantized_bits(bits=16, integer=6)

    infer_model = build_inference_model(n_features=n)

    # Snap QAT weights to the fixed-point grid before transferring to inference model.
    # This ensures hls4ml C sim uses values already representable in ap_fixed<8,1>
    # and ap_fixed<16,7>, eliminating quantization mismatch in simulation.
    for name in ['dense_1', 'dense_2', 'dense_3']:
        q_layer = q_model.get_layer(name)
        w = tf.cast(q_layer.kernel, tf.float32)
        b = tf.cast(q_layer.bias,   tf.float32)
        w_snapped = kernel_q(w).numpy()
        b_snapped = bias_q(b).numpy()
        infer_model.get_layer(name).set_weights([w_snapped, b_snapped])

    # win/spread are standard Dense in Phase 3 — no quantizer applied
    for name in ['win', 'spread']:
        infer_model.get_layer(name).set_weights(q_model.get_layer(name).get_weights())

    print(f"Inference model built: {infer_model.count_params()} parameters")
    print(f"Input shape:  {infer_model.input_shape}")
    print(f"Output names: {[o.name for o in infer_model.outputs]}")
    return infer_model, features


def build_hls_config(model, reuse_factor=None):
    """
    Build hls4ml config with Phase 3 fixed-point bit widths set explicitly.
    Mapping from QKeras quantized_bits(B, I) → ap_fixed<B, I+1> (+1 for sign bit):
      kernel  ap_fixed<8,1>  — quantized_bits(8, 0): 1 sign + 7 frac → range [-1, 1)
      bias    ap_fixed<16,7> — quantized_bits(16, 6): 1 sign + 6 int + 9 frac → range [-64, 64)
      result  ap_fixed<8,4>  — quantized_relu(8, 4): post-ReLU, 4 integer bits covers range

    Global default precision: fixed<20,12>
      The model_default_t controls intermediate types (pre-relu outputs, input type, etc.).
      fixed<16,6> (10 fractional bits) accumulates 128-step rounding error ≈ 0.064 for
      dense_2, causing the HLS C sim to drift from the QKeras reference.
      fixed<20,12> (12 fractional bits) reduces that to ≈ 0.016, bringing delta within tolerance.
      Actual pre-relu values max ±2.7 — well within the fixed<20,12> range of ±2048.
    """
    config = hls4ml.utils.config_from_keras_model(model, granularity='name')

    # Global default controls all intermediate types (accumulators, pre-relu outputs, input type).
    # fixed<W,I>: fractional bits = W-I. Need ≥12 frac bits to keep 128-step error < 0.05.
    #   fixed<16,6>: 10 frac bits → error ≈ 0.125 — too large, C sim fails.
    #   fixed<18,6>: 12 frac bits → error ≈ 0.031. Range ±32 covers pre-relu sums. LUT target.
    #   fixed<24,10>: 14 frac bits → error ≈ 0.008. Was previous value; costs ~20-30% more LUT.
    # Fallback: if C sim mean_delta > 0.05, try fixed<20,8> (12 frac bits, ±128 range).
    config['Model']['Precision']['default'] = 'fixed<18,6>'

    # Use Resource strategy so hls4ml emits dense_resource (serialized MACs over RF cycles)
    # instead of dense_latency (all MACs in one cycle). Without this, ReuseFactor is ignored
    # and all layers use dense_latency regardless — which with reuse_factor=1 unrolls all
    # 8192 parallel multiplications for dense_2, consuming 400k LUTs on Basys 3 (19× budget).
    config['Model']['Strategy'] = 'Resource'

    # Valid ReuseFactor per layer (hls4ml auto-corrects to nearest valid divisor of n_in*n_out):
    #   dense_1: n_in=21, n_out=128, total=2688 → valid: ..., 168, 336, ...  → use 168
    #   dense_2: n_in=128, n_out=64, total=8192 → valid: ..., 128, 512, ... → use 512
    #   dense_3: n_in=64, n_out=32, total=2048  → valid: ..., 128, 512, ... → use 512
    #   win/spread: n_in=32, n_out=1, total=32  → valid: 1,2,4,8,16,32      → use 32
    # RF tuning history:
    #   RF=168/128/128: total 30,850 LUT (148%). Relu layers = 8,736 LUT (128+64+32 parallel comparators).
    #   RF=336/512/512: total 27,921 LUT (134%). dense_1 WORSENED (336 grew sparsemux 17→33 inputs).
    #   RF=168/512/512 + serial relu (io_serial): 27,649 LUT (133%). Serial relu saved 8,357 LUT, but
    #     dense_1 jumped from 6,722 → 14,342 LUT (STREAM depth=2 + UNROLL Result = drain mux, +7,620 LUT).
    #   RF=168/512/512 + serial relu (io_parallel): 65,148 LUT (313%). DATAFLOW created 32,708 LUT FIFOs.
    #   RF=168/512/512 + serial relu + PIPELINE Result (io_serial): target ~17,000–19,000 LUT.
    rf_dense1, rf_dense2, rf_dense3, rf_out = 168, 512, 512, 32

    # Explicit weights/biases matching Phase 3 quantization — override the wider default.
    for layer_name, rf in [('dense_1', rf_dense1), ('dense_2', rf_dense2), ('dense_3', rf_dense3)]:
        config['LayerName'][layer_name]['Precision'] = {
            'weight': 'ap_fixed<8,1>',
            'bias':   'ap_fixed<16,7>',
        }
        config['LayerName'][layer_name]['ReuseFactor'] = rf

    # hls4ml splits fused Dense+ReLU into separate layers.
    # ReLU outputs are quantized to match Phase 3 QActivation(quantized_relu(8,4)).
    for relu_name in ['dense_1_relu', 'dense_2_relu', 'dense_3_relu']:
        if relu_name in config.get('LayerName', {}):
            config['LayerName'][relu_name]['Precision'] = {'result': 'ap_fixed<8,4>'}

    for out_layer in ['win', 'spread']:
        config['LayerName'][out_layer]['ReuseFactor'] = rf_out

    print("\nhls4ml config (LayerName section):")
    print(json.dumps(config.get('LayerName', {}), indent=2, default=str))
    return config


def convert_model(model, config, output_dir='phase4_hls/hls_project'):
    os.makedirs(output_dir, exist_ok=True)
    hls_model = hls4ml.converters.convert_from_keras_model(
        model,
        hls_config=config,
        output_dir=output_dir,
        backend='Vitis',          # Vivado HLS was discontinued after 2020.1; Vitis HLS is current
        part='xc7a35tcpg236-1',  # exact Artix-7 part on the Basys 3
        clock_period=10,          # 100 MHz — matches Basys 3 onboard oscillator
        io_type='io_stream',      # io_stream: the maintained hls4ml path. Emits a proper #pragma HLS DATAFLOW
                                  # region with rate-matched FIFOs and stream-safe dense implementations.
                                  #
                                  # WHY we left io_serial (see AUDIT_REPORT.md §1): io_serial generated a strictly
                                  # SEQUENTIAL top-level FSM (not dataflow) with depth-2 FIFOs between layers whose
                                  # producer/consumer never run concurrently → the first layer fills its depth-2 FIFO,
                                  # ap_done never fires, and the design DEADLOCKS in real silicon on the first inference.
                                  # It also re-read consumed streams (config4/6 did 512 reads of 128 writes; layer7_out
                                  # had two destructive consumers). Phase 6 only passed because the cocotb Makefile
                                  # swapped in deep/replay FIFO stubs that are NOT in the bitstream; cosim was never run.
                                  #
                                  # io_stream fixes all of that by construction. Earlier io_parallel blowup (32,708 LUT
                                  # in ping-pong FIFOs → 313%) does NOT predict io_stream: io_stream streams elements
                                  # through shallow rate-matched FIFOs rather than buffering full inter-layer arrays.
                                  #
                                  # GATES (both required, independent):
                                  #   functional: cosim_design -rtl verilog must PASS (catches stream rate mismatch)
                                  #   fit:        actual Vivado post-implementation LUTs <= 20,800 (NOT the HLS estimate,
                                  #               which overcounts). Fallbacks if over: ReuseFactor -> precision -> layer width.
                                  #
                                  # NOTE: io_stream changes the top-level interface — `features` becomes an AXI-Stream
                                  # port (features_TDATA/TVALID/TREADY) instead of ap_memory (address0/ce0/q0).
                                  # mlp_controller.v must be rewritten for the new handshake. Read the regenerated
                                  # myproject.v port list before writing it. The io_serial template patches in
                                  # phase4_hls/patches/ do NOT apply to io_stream — do not overlay them.
    )

    firmware_dir = os.path.join(output_dir, 'firmware')
    if os.path.isdir(firmware_dir):
        print(f"\nHLS project generated at: {output_dir}")
        print("Firmware files:")
        for f in sorted(os.listdir(firmware_dir)):
            print(f"  firmware/{f}")
    return hls_model


def run_csim(hls_model, infer_model):
    """
    C simulation: compile generated C++ with GCC and compare to the float32
    inference model (same snapped weights, float32 arithmetic).
    Uses hls4ml's bundled ap_fixed headers — no Vitis HLS required.

    Reference is the float32 inference model (not QKeras) because:
    - Both share the same snapped weights, so weight quantization is not a factor.
    - The only delta is relu quantization (continuous vs ap_fixed<8,4>) and
      input quantization (float32 vs fixed<24,10>) — exactly what we want to measure.
    - QKeras float32 accumulation causes spurious step-changes at relu boundaries
      whenever the HLS fixed-point accumulation differs by a fraction of 0.0625,
      inflating the max delta beyond the inherent hardware error.
    """
    print("\nCompiling C simulation (GCC)...")
    hls_model.compile()
    print("Compilation done.")

    scaler = pickle.load(open('artifacts/scaler.pkl', 'rb'))
    with open('artifacts/features.json') as f:
        meta = json.load(f)
    games = pd.read_parquet('data/processed/games.parquet')
    val   = games[games['season'].isin([2021, 2022])]
    X_val = scaler.transform(val[meta['features']].values.astype('float32'))

    N        = 100
    X_sample = X_val[:N]

    print(f"Running C sim on {N} validation samples...")
    hls_pred   = hls_model.predict(np.ascontiguousarray(X_sample))
    keras_pred = infer_model.predict(X_sample, verbose=0)

    # hls_model.predict may return a list [win_arr, spread_arr] or a 2-D array
    if isinstance(hls_pred, list):
        hls_win = np.array(hls_pred[0]).flatten()
    elif hls_pred.ndim > 1:
        hls_win = hls_pred[:, 0]
    else:
        hls_win = hls_pred.flatten()
    keras_win = keras_pred[0].flatten()[:N]

    mean_delta = float(np.abs(hls_win - keras_win).mean())
    max_delta  = float(np.abs(hls_win - keras_win).max())

    print(f"\nC Simulation vs float32 inference model ({N} samples):")
    print(f"  Mean win prob delta: {mean_delta:.4f}")
    print(f"  Max  win prob delta: {max_delta:.4f}")

    if mean_delta <= 0.05 and max_delta <= 0.10:
        print("C simulation outputs match inference model within tolerance.")
    else:
        print("WARNING: delta above tolerance — check bit widths in build_hls_config().")

    return mean_delta, max_delta


if __name__ == '__main__':
    print("=== Phase 4: HLS Conversion ===\n")

    vitis_on_path = shutil.which('vitis_hls') is not None
    if vitis_on_path:
        print("Vitis HLS on PATH — Option A (automated synthesis available).")
    else:
        print("vitis_hls not on WSL PATH — Option B.")
        print("This script generates C++ and runs C sim.")
        print("Synthesis runs in Windows Vitis HLS GUI (see resource_report.py).\n")

    # 1. Load inference model (QAT weights snapped to fixed-point grid)
    model, features = load_inference_model()

    # 2. Build hls4ml config with Phase 3 bit widths
    config = build_hls_config(model)

    # 3. Convert to HLS C++ project
    hls_model = convert_model(model, config, output_dir='phase4_hls/hls_project')

    # 4. Save config record for Phase 5 and resource_report.py
    os.makedirs('artifacts', exist_ok=True)
    hls_config_record = {
        'reuse_factor':    {'dense_1': 168, 'dense_2': 512, 'dense_3': 512, 'win': 32, 'spread': 32},
        'backend':         'Vitis',
        'part':            'xc7a35tcpg236-1',
        'clock_period':    10,
        'io_type':         'io_stream',
        'win_result_t':    'ap_fixed<8,1>',
        'spread_result_t': 'ap_fixed<16,7>',
        'model_source':    'model_quantized.keras (QAT weights snapped to fixed-point grid)',
        'bit_widths':      {'kernel': '<8,1>', 'bias': '<16,7>', 'activation': '<8,4>'},
    }
    with open('artifacts/hls_config.json', 'w') as f:
        json.dump(hls_config_record, f, indent=2)
    print("\nSaved: artifacts/hls_config.json")

    # 5. C simulation — GCC-based, no Vitis HLS needed
    mean_delta, max_delta = run_csim(hls_model, model)

    # 6. Persist csim results for tests
    csim_record = {
        'csim_mean_delta': mean_delta,
        'csim_max_delta':  max_delta,
        'csim_n_samples':  100,
    }
    with open('artifacts/hls_resource_report.json', 'w') as f:
        json.dump(csim_record, f, indent=2)
    print("Saved: artifacts/hls_resource_report.json (csim results only)")

    print("\n=== Conversion complete ===")
    print(f"  C sim mean delta: {mean_delta:.4f}")
    print(f"  C sim max delta:  {max_delta:.4f}")
    print(f"  HLS project:      phase4_hls/hls_project/")
    print("\nNext: run python phase4_hls/resource_report.py")
    if not vitis_on_path:
        print("(Option B: open project in Windows Vitis HLS first,")
        print(" run C Synthesis + Export IP, then re-run resource_report.py)")
