"""
Evaluation utilities for comparing full-precision vs quantized models.
"""

import json

import tensorflow as tf
tf.config.run_functions_eagerly(True)  # QKeras 0.9 needs eager mode for .numpy() in quantizers

import numpy as np


def evaluate_both_models(fp32_model, q_model, X_val, y_val):
    """
    Print a side-by-side comparison table and return metrics dict.
    Warns if accuracy drop exceeds the 2% tolerance for Phase 4 readiness.
    """
    fp_preds = fp32_model.predict(X_val, verbose=0)
    q_preds  = q_model.predict(X_val, verbose=0)

    fp_win_acc    = ((fp_preds[0].flatten() >= 0.5) == y_val['win']).mean()
    q_win_acc     = ((q_preds[0].flatten()  >= 0.5) == y_val['win']).mean()
    fp_spread_mae = np.abs(fp_preds[1].flatten() - y_val['spread']).mean()
    q_spread_mae  = np.abs(q_preds[1].flatten()  - y_val['spread']).mean()
    prob_delta    = np.abs(fp_preds[0].flatten() - q_preds[0].flatten()).mean()
    acc_drop      = fp_win_acc - q_win_acc

    print(f"\n{'Metric':<25} {'Full Precision':>15} {'Quantized':>12} {'Delta':>10}")
    print("-" * 65)
    print(f"{'Win Accuracy':<25} {fp_win_acc:>15.3f} {q_win_acc:>12.3f} {acc_drop:>+10.3f}")
    print(f"{'Spread MAE':<25} {fp_spread_mae:>15.2f} {q_spread_mae:>12.2f} "
          f"{q_spread_mae - fp_spread_mae:>+10.2f}")
    print(f"{'Mean Prob Delta':<25} {'—':>15} {prob_delta:>12.4f}")

    if acc_drop > 0.02:
        print(f"\nWARNING: accuracy drop {acc_drop:.1%} exceeds 2% tolerance.")
        print("Action: widen kernel quantizer to <16,6> and re-run.")
    else:
        print(f"\nAccuracy drop within tolerance ({acc_drop:.1%} <= 2.0%)")

    return {
        'fp_win_acc':    fp_win_acc,
        'q_win_acc':     q_win_acc,
        'fp_spread_mae': fp_spread_mae,
        'q_spread_mae':  q_spread_mae,
        'prob_delta':    prob_delta,
        'acc_drop':      acc_drop,
    }


def run_sample_comparison(fp32_model, q_model, X_val, n=10):
    """Print per-game win probability and spread for n evenly-spaced val games."""
    indices = np.linspace(0, len(X_val) - 1, n, dtype=int)
    fp_p    = fp32_model.predict(X_val, verbose=0)
    q_p     = q_model.predict(X_val, verbose=0)

    print(f"\n=== Sample-by-sample comparison ({n} games) ===")
    print(f"{'idx':>5}  {'FP Win%':>8}  {'Q Win%':>8}  {'Delta':>7}  "
          f"{'FP Sprd':>8}  {'Q Sprd':>8}")
    print("-" * 58)
    for i in indices:
        print(f"{i:>5}  {fp_p[0][i][0]:>8.1%}  {q_p[0][i][0]:>8.1%}  "
              f"{q_p[0][i][0] - fp_p[0][i][0]:>+7.1%}  "
              f"{fp_p[1][i][0]:>+8.1f}  {q_p[1][i][0]:>+8.1f}")


def save_quantization_report(metrics, bit_config):
    """Save quantization_report.json to artifacts/."""
    report = {
        'bit_config':       bit_config,
        'fp_win_accuracy':  round(float(metrics['fp_win_acc']),    4),
        'q_win_accuracy':   round(float(metrics['q_win_acc']),     4),
        'accuracy_drop':    round(float(metrics['acc_drop']),      4),
        'fp_spread_mae':    round(float(metrics['fp_spread_mae']), 4),
        'q_spread_mae':     round(float(metrics['q_spread_mae']),  4),
        'mean_prob_delta':  round(float(metrics['prob_delta']),    4),
        'within_tolerance': bool(metrics['acc_drop'] <= 0.02),
        'ready_for_phase4': bool(metrics['q_win_acc'] >= 0.63
                                 and metrics['acc_drop'] <= 0.02),
    }

    with open('artifacts/mlp/quantization_report.json', 'w') as f:
        json.dump(report, f, indent=2)

    print(f"\nSaved: artifacts/mlp/quantization_report.json")
    print(f"Ready for Phase 4: {report['ready_for_phase4']}")
    return report
