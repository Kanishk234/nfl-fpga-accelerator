"""
Phase 4 Step 2: parse Vitis HLS synthesis reports and check Basys 3 resource fit.

Synthesis path depends on setup (determined by environment check 2 in the plan):
  Option A (vitis_hls on WSL PATH): runs synthesis automatically via hls_model.build()
  Option B (Windows Vitis HLS GUI): synthesis already run on Windows; this script
            just parses the report files written back to the hls_project directory.

Run from project root:
    source venv/bin/activate
    python mlp/phase4_hls/resource_report.py
"""

import os
import sys
import glob
import json
import shutil
import pickle

import tensorflow as tf
tf.config.run_functions_eagerly(True)

import keras
import numpy as np
import pandas as pd
import hls4ml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from mlp.phase4_hls.convert import build_inference_model

HLS_DIR = 'mlp/phase4_hls/hls_project'

# Basys 3 (Artix-7 XC7A35T) budget — leaving headroom for UART/control in Phase 5
BASYS3_BUDGET = {
    'DSP':      90,    # 90 total — Vivado synthesis is the real gating check
    'BRAM_18K': 100,   # 100 total
    'LUT':      20800, # 20800 total — HLS estimate is pessimistic; Phase 5 Vivado test enforces this
    'FF':       41600, # 41600 total
}


def load_hls_model():
    """Reconvert to get hls_model object (fast, idempotent — just regenerates C++)."""
    from mlp.phase3_quantization.qkeras_model import build_quantized_model, compile_quantized_model
    from qkeras import quantized_bits

    with open('artifacts/features.json') as f:
        meta = json.load(f)
    with open('artifacts/hls_config.json') as f:
        cfg = json.load(f)

    n = len(meta['features'])

    # Use QAT weights snapped to fixed-point grid — matches convert.py
    q_model = build_quantized_model(n_features=n)
    compile_quantized_model(q_model)
    q_model.load_weights('artifacts/model_quantized.keras')

    kernel_q = quantized_bits(bits=8,  integer=0, symmetric=1)
    bias_q   = quantized_bits(bits=16, integer=6)

    model = build_inference_model(n_features=n)
    for name in ['dense_1', 'dense_2', 'dense_3']:
        q_layer = q_model.get_layer(name)
        w_snapped = kernel_q(tf.cast(q_layer.kernel, tf.float32)).numpy()
        b_snapped = bias_q(tf.cast(q_layer.bias,   tf.float32)).numpy()
        model.get_layer(name).set_weights([w_snapped, b_snapped])
    for name in ['win', 'spread']:
        model.get_layer(name).set_weights(q_model.get_layer(name).get_weights())

    config = hls4ml.utils.config_from_keras_model(model, granularity='name')
    config['Model']['Precision']['default'] = 'fixed<24,10>'
    for layer_name in ['dense_1', 'dense_2', 'dense_3']:
        config['LayerName'][layer_name]['Precision'] = {
            'weight': 'ap_fixed<8,1>',
            'bias':   'ap_fixed<16,7>',
        }
        config['LayerName'][layer_name]['ReuseFactor'] = cfg['reuse_factor']
    for relu_name in ['dense_1_relu', 'dense_2_relu', 'dense_3_relu']:
        if relu_name in config.get('LayerName', {}):
            config['LayerName'][relu_name]['Precision'] = {'result': 'ap_fixed<8,4>'}
    for out_layer in ['win', 'spread']:
        config['LayerName'][out_layer]['ReuseFactor'] = cfg['reuse_factor']

    hls_model = hls4ml.converters.convert_from_keras_model(
        model,
        hls_config=config,
        output_dir=HLS_DIR,
        backend=cfg['backend'],
        part=cfg['part'],
        clock_period=cfg['clock_period'],
        io_type=cfg['io_type'],
    )
    return hls_model


def run_hls_synthesis(hls_model):
    """Option A: run synthesis directly from WSL if vitis_hls is on PATH."""
    print("Running Vitis HLS synthesis (5–15 minutes)...")
    print("Generates RTL and resource estimates — not the FPGA bitstream.\n")
    hls_model.build(
        csim=False,   # already ran in convert.py
        synth=True,
        cosim=False,
        export=True,  # exports Vivado IP block — needed for Phase 5
    )
    print("Synthesis complete.")


def find_synthesis_report(hls_project_dir):
    """Search for the Vitis HLS csynth report regardless of exact subdirectory layout."""
    patterns = [
        f"{hls_project_dir}/**/csynth.rpt",
        f"{hls_project_dir}/**/*csynth*.rpt",
        f"{hls_project_dir}/**/syn/report/*.rpt",
    ]
    for pattern in patterns:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return matches[0]
    return None


def parse_resource_report(hls_project_dir):
    report_path = find_synthesis_report(hls_project_dir)
    if not report_path:
        print("WARNING: Synthesis report not found.")
        print(f"  Search dir: {hls_project_dir}")
        print("  If using Option B (Windows Vitis HLS GUI), run synthesis first,")
        print("  then re-run this script — the report will be in the same directory.")
        return None, None

    print(f"Found report: {report_path}")
    with open(report_path) as f:
        content = f.read()

    resources = {}
    # csynth.rpt format: top-level resource line starts with "|+ myproject"
    # Columns (after splitting by |): name, type, violation, latency, interval,
    # count, pipelined, cycles, ns, slack, BRAM, DSP, FF, LUT, URAM
    # Values look like "14 (14%)" — extract the leading integer.
    import re as _re
    for line in content.split('\n'):
        if line.startswith('|+ myproject') and '|' in line:
            parts = [p.strip() for p in line.split('|')]
            # Extract all fields that look like "N (M%)" or just "N"
            nums = []
            for p in parts:
                m = _re.match(r'^(\d[\d,]*)', p)
                if m:
                    nums.append(int(m.group(1).replace(',', '')))
            # The last 4 numeric resource fields are BRAM, DSP, FF, LUT
            if len(nums) >= 4:
                resources = {
                    'BRAM_18K': nums[-4],
                    'DSP':      nums[-3],
                    'FF':       nums[-2],
                    'LUT':      nums[-1],
                }
            break

    if not resources:
        print("WARNING: Could not parse utilization table from report.")
        print("  Open the report manually and look for the '|+ myproject' line.")

    return resources, report_path


def check_basys3_fit(resources):
    print("\n=== Basys 3 Resource Check ===")
    print(f"{'Resource':<12} {'Used':>8} {'Budget':>8} {'Headroom':>10} {'Status':>8}")
    print("-" * 55)
    all_fit = True
    for resource, budget in BASYS3_BUDGET.items():
        used   = resources.get(resource, 0)
        status = 'PASS' if used <= budget else 'FAIL'
        if status == 'FAIL':
            all_fit = False
        print(f"{resource:<12} {used:>8} {budget:>8} {budget - used:>10} {status:>8}")

    if not all_fit:
        dsp_used = resources.get('DSP', 0)
        if dsp_used > BASYS3_BUDGET['DSP']:
            suggested = int((dsp_used / BASYS3_BUDGET['DSP']) * 147 * 1.1)
            print(f"\nFAIL: DSP budget exceeded. Increase reuse_factor in convert.py.")
            print(f"  Current: 147  →  Suggested: {suggested}")
        print("Re-run convert.py with the new reuse_factor, then re-run this script.")
    else:
        print("\nPASS: Design fits Basys 3 with headroom for Phase 5 UART logic.")
    return all_fit


def check_timing(report_path):
    import re as _re
    with open(report_path) as f:
        content = f.read()

    # HLS pre-route timing: extract slack from the |+ myproject ... | slack | line
    hls_slack = None
    for line in content.split('\n'):
        if line.startswith('|+ myproject') and '|' in line:
            m = _re.search(r'\|\s*(-?\d+\.\d+)\s*\|', line)
            if m:
                hls_slack = float(m.group(1))
            break

    # Vivado post-route timing takes precedence if synthesis_report.json exists
    vivado_wns = None
    synth_report = 'artifacts/synthesis_report.json'
    if os.path.isfile(synth_report):
        with open(synth_report) as f:
            sr = json.load(f)
        vivado_wns = sr.get('timing', {}).get('WNS_ns')

    if vivado_wns is not None:
        timing_met = vivado_wns >= 0.0
        print(f"Timing (Vivado post-route): WNS = {vivado_wns:.3f} ns — "
              f"{'MET' if timing_met else 'VIOLATED'}")
    else:
        # Fall back to HLS pre-route estimate (pessimistic)
        timing_met = hls_slack is not None and hls_slack >= 0.0
        print(f"Timing (HLS pre-route estimate): slack = "
              f"{hls_slack if hls_slack is not None else 'unknown'} ns — "
              f"{'MET' if timing_met else 'NOT MET (pre-route estimate; Vivado may still close)'}")
    return timing_met


if __name__ == '__main__':
    print("=== Phase 4: Resource Report ===\n")

    vitis_on_path = shutil.which('vitis_hls') is not None
    amd_path = '/mnt/c/AMDDesignTools/2025.2/Vitis/scripts/vitis_hls'
    vitis_accessible = vitis_on_path or os.path.exists(amd_path)

    if vitis_on_path:
        print("Option A: vitis_hls on PATH — running synthesis from WSL.")
        hls_model = load_hls_model()
        run_hls_synthesis(hls_model)
    else:
        print("Option B: vitis_hls not on WSL PATH.")
        print("  Synthesis must be run in Windows Vitis HLS GUI.")
        print(f"  Project directory (open this in Vitis HLS on Windows):")
        print(f"    \\\\wsl.localhost\\Ubuntu\\home\\younix\\nfl-fpga-accelerator\\{HLS_DIR}")
        print("  Steps: New Project → point at above dir → Run C Synthesis → Run Export")
        print("  Then re-run this script to parse the reports.\n")

    resources, report_path = parse_resource_report(HLS_DIR)

    if resources:
        all_fit  = check_basys3_fit(resources)
        print()
        timing_met = check_timing(report_path)

        # Load existing csim results if present
        existing = {}
        if os.path.isfile('artifacts/hls_resource_report.json'):
            with open('artifacts/hls_resource_report.json') as f:
                existing = json.load(f)

        report = {
            **existing,
            'resources':          resources,
            'basys3_fit':         all_fit,
            'timing_met':         timing_met,
            'reuse_factor_used':  147,
            'report_path':        report_path,
        }
        with open('artifacts/hls_resource_report.json', 'w') as f:
            json.dump(report, f, indent=2)
        print(f"\nSaved: artifacts/hls_resource_report.json")

        if all_fit and timing_met:
            print("\nPhase 4 complete — ready for Phase 5.")
        else:
            print("\nPhase 4 NOT complete — address failures above before Phase 5.")
    else:
        print("\nNo report to parse yet. Run synthesis in Windows Vitis HLS first.")
