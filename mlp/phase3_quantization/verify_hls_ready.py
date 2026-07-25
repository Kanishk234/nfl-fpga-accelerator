"""
hls4ml readiness checks for the quantized model.

Run at the end of quantize.py after the model and report are saved.
All hard checks must pass before Phase 4 begins — better to catch
incompatibilities here than mid-synthesis.

Checks:
  1. Layer types are in the hls4ml-supported set
  2. Hidden layer activations are ReLU only
  3. Bit widths are within the 1–16 range hls4ml accepts
  4. No Dropout layers present
  5. DSP resource estimate vs Basys 3 budget (informational — never fails)
"""

import json

import tensorflow as tf
tf.config.run_functions_eagerly(True)  # QKeras 0.9 needs eager mode for .numpy() in quantizers

from keras.layers import Dense
from qkeras import QDense, QActivation


def verify_hls_ready(model, quantization_report):
    """
    Run all hls4ml readiness checks. Updates quantization_report.json in place
    with hls4ml_ready, hls4ml_checks, est_dsps_unrolled, and
    recommended_reuse_factor fields.

    Raises AssertionError if any hard check fails.
    Returns the results dict.
    """
    results = {}

    # CHECK 1: Layer types are all hls4ml-supported
    supported_types = {'QDense', 'QActivation', 'Dense', 'InputLayer'}
    bad_layers = [
        f"{l.name} ({type(l).__name__})"
        for l in model.layers
        if type(l).__name__ not in supported_types
    ]
    assert not bad_layers, \
        f"Unsupported layer types (hls4ml cannot synthesize): {bad_layers}"
    results['layer_types'] = 'PASS'
    print("CHECK 1 — Layer types:        PASS")

    # CHECK 2: No unsupported activations in hidden layers
    # hls4ml supports relu natively; sigmoid only on output; nothing else.
    for layer in model.layers:
        if layer.name not in ('dense_1', 'dense_2', 'dense_3'):
            continue
        act = getattr(layer, 'activation', None)
        if act is None:
            continue
        act_name = getattr(act, '__name__', str(act)).lower()
        assert 'relu' in act_name or act_name == 'linear', \
            f"Hidden layer {layer.name} has activation '{act_name}' — must be relu"
    results['activations'] = 'PASS'
    print("CHECK 2 — Activations:        PASS")

    # CHECK 3: Bit widths within hls4ml accepted range (1–16)
    def extract_bits(bit_str):
        return int(bit_str.strip('<>').split(',')[0])

    kernel_bits = quantization_report['bit_config']['kernel']
    bias_bits   = quantization_report['bit_config']['bias']
    act_bits    = quantization_report['bit_config']['activation']

    assert 1 <= extract_bits(kernel_bits) <= 16, \
        f"Kernel bits out of hls4ml range [1,16]: {kernel_bits}"
    assert 1 <= extract_bits(bias_bits) <= 16, \
        f"Bias bits out of hls4ml range [1,16]: {bias_bits}"
    assert 1 <= extract_bits(act_bits) <= 16, \
        f"Activation bits out of hls4ml range [1,16]: {act_bits}"
    results['bit_widths'] = 'PASS'
    print("CHECK 3 — Bit widths:         PASS")

    # CHECK 4: No Dropout layers
    dropout_layers = [l.name for l in model.layers if 'dropout' in l.name.lower()]
    assert not dropout_layers, \
        f"Dropout layers found — remove before Phase 4: {dropout_layers}"
    results['no_dropout'] = 'PASS'
    print("CHECK 4 — No dropout:         PASS")

    # CHECK 5: DSP resource estimate (informational — never fails the gate)
    total_weights = sum(
        l.count_params()
        for l in model.layers
        if isinstance(l, (QDense, Dense))
    )
    est_dsps = total_weights  # worst-case: one DSP per weight at reuse_factor=1

    print(f"CHECK 5 — Resource estimate:")
    print(f"  Total weights:         {total_weights:,}")
    print(f"  Est. DSPs (unrolled):  {est_dsps:,}  (Basys 3 budget: 90)")

    if est_dsps > 90:
        rec_reuse = est_dsps // 90 + 1
        print(f"  NOTE: Exceeds 90 DSPs at reuse_factor=1. Expected for this model size.")
        print(f"  hls4ml reuse_factor will time-multiplex DSPs to fit.")
        print(f"  Recommended starting point: reuse_factor = {rec_reuse}")
        results['resource_estimate'] = 'NOTE'
    else:
        rec_reuse = 1
        print(f"  Fits within 90 DSPs at reuse_factor=1 — fully unrolled possible.")
        results['resource_estimate'] = 'PASS'

    # Summary
    hard_checks = ['layer_types', 'activations', 'bit_widths', 'no_dropout']
    all_pass = all(results[k] == 'PASS' for k in hard_checks)
    print(f"\nhls4ml readiness: {'ALL CHECKS PASS' if all_pass else 'FAILURES DETECTED — fix before Phase 4'}")

    # Update quantization_report.json with hls4ml fields
    quantization_report['hls4ml_ready']              = all_pass
    quantization_report['hls4ml_checks']             = results
    quantization_report['est_dsps_unrolled']         = int(est_dsps)
    quantization_report['recommended_reuse_factor']  = int(rec_reuse)

    with open('artifacts/mlp/quantization_report.json', 'w') as f:
        json.dump(quantization_report, f, indent=2)

    assert all_pass, "hls4ml readiness checks failed — see output above"
    return results
