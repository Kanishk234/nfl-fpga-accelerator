"""
Phase 4 (conifer) — Step 2a: emit Vitis HLS projects for both GBDT stages.

Generates two synthesizable HLS projects (win + spread) at the locked
precision from the Step-1 scan. Synthesis itself runs on Windows via
run_synthesis.bat (same Vitis 2025.2 toolchain + C:\\Temp copy dance as the
MLP flow — see mlp/phase4_hls/run_synthesis.bat for why).

Run from project root (WSL):
    python gbdt/phase4_hls/convert_hls.py
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import xgboost as xgb

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import conifer

PRECISION = 'ap_fixed<24,12>'   # locked by precision_scan.py — see PHASE4_CONIFER_COMPLETE.md
PART = 'xc7a35tcpg236-1'        # Basys 3 (Artix-7), same as the MLP flow
CLOCK_PERIOD = '10'             # 100 MHz, same as the MLP flow


def patch_testbench(outdir, project):
    """conifer 1.9 template bug: header + testbench carry a third tree_scores
    debug argument that the generated firmware .cpp (the synthesis truth) does
    not define -> csim compile/link errors. Align both to the 2-arg firmware
    signature; the (all-zero) tree_scores array and its log writes are left."""
    tb = os.path.join(outdir, f'{project}_test.cpp')
    with open(tb) as f:
        src = f.read()
    patched = src.replace(f'{project}(x, score, tree_scores);',
                          f'{project}(x, score);')
    assert patched != src, f'testbench call pattern not found in {tb}'
    with open(tb, 'w') as f:
        f.write(patched)

    hdr = os.path.join(outdir, 'firmware', f'{project}.h')
    with open(hdr) as f:
        src = f.read()
    patched = src.replace(
        ',\n\tscore_t tree_scores[BDT::fn_classes(n_classes) * n_trees]);',
        ');')
    assert patched != src, f'header prototype pattern not found in {hdr}'
    with open(hdr, 'w') as f:
        f.write(patched)
    print(f"  patched {tb} + {hdr} (dropped tree_scores arg)")

    # Project convention is Verilog (UART wrapper, MLP flow, cosim all Verilog);
    # conifer's stock vivado_synth.tcl points at the equivalent vhdl output.
    vs = os.path.join(outdir, 'vivado_synth.tcl')
    with open(vs) as f:
        src = f.read()
    patched = src.replace('syn/vhdl', 'syn/verilog')
    assert patched != src, f'vhdl path not found in {vs}'
    with open(vs, 'w') as f:
        f.write(patched)
    print(f"  patched {vs} (vhdl -> verilog)")


def emit(booster, name):
    cfg = conifer.backends.xilinxhls.auto_config()
    cfg['Precision'] = PRECISION
    cfg['XilinxPart'] = PART
    cfg['ClockPeriod'] = CLOCK_PERIOD
    cfg['ProjectName'] = f'conifer_{name}'
    cfg['OutputDir'] = f'gbdt/phase4_hls/hls_{name}'
    model = conifer.converters.convert_from_xgboost(booster, cfg)
    model.write()
    patch_testbench(cfg['OutputDir'], cfg['ProjectName'])
    print(f"  wrote {cfg['OutputDir']}/ (top: {cfg['ProjectName']})")
    return model


if __name__ == '__main__':
    print("=== Phase 4 (conifer) HLS project generation ===")
    print(f"precision {PRECISION} | part {PART} | clock {CLOCK_PERIOD} ns\n")

    win = xgb.XGBClassifier()
    win.load_model('artifacts/gbdt/win_model.json')
    spread = xgb.XGBRegressor()
    spread.load_model('artifacts/gbdt/spread_model.json')

    print("[1/2] win classifier")
    emit(win.get_booster(), 'win')
    print("[2/2] spread residual regressor")
    emit(spread.get_booster(), 'spread')

    print("\nNext: run gbdt/phase4_hls/run_synthesis.bat on Windows.")
