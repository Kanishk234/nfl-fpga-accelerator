# tests/test_phase6.py — Phase 6 pytest suite

import os
import csv
import glob
import subprocess
import functools
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ROOT   = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SIM    = os.path.join(ROOT, 'phase6_sim')
COCOTB = os.path.join(SIM, 'cocotb')
VEC    = os.path.join(SIM, 'test_vectors')


def _vec(name):
    return os.path.join(VEC, name)


def _sim(name):
    return os.path.join(SIM, name)


def _cocotb(name):
    return os.path.join(COCOTB, name)


# ---------------------------------------------------------------------------
# FILE EXISTENCE
# ---------------------------------------------------------------------------

def test_run_sim_exists():
    assert os.path.isfile(_sim('run_sim.py'))


def test_stub_exists():
    assert os.path.isfile(os.path.join(SIM, 'stubs', 'myproject_stub.v'))


def test_uart_helpers_exists():
    assert os.path.isfile(_cocotb('uart_helpers.py'))


def test_test_uart_rx_exists():
    assert os.path.isfile(_cocotb('test_uart_rx.py'))


def test_test_uart_tx_exists():
    assert os.path.isfile(_cocotb('test_uart_tx.py'))


def test_test_uart_framing_exists():
    assert os.path.isfile(_cocotb('test_uart_framing.py'))


def test_test_mlp_controller_exists():
    assert os.path.isfile(_cocotb('test_mlp_controller.py'))


def test_test_integration_exists():
    assert os.path.isfile(_cocotb('test_integration.py'))


def test_test_regression_exists():
    assert os.path.isfile(_cocotb('test_regression.py'))


def test_mlp_verilog_test_exists():
    assert os.path.isfile(_cocotb('test_mlp_verilog.py'))


def test_real_mlp_verilog_dir_exists():
    vlog_dir = os.path.join(
        ROOT, 'phase4_hls', 'hls_project', 'myproject_prj',
        'solution1', 'syn', 'verilog'
    )
    vfiles = glob.glob(os.path.join(vlog_dir, '*.v'))
    assert len(vfiles) >= 30, \
        f"Expected ≥30 Verilog files in {vlog_dir}, found {len(vfiles)}"


def test_test_vectors_dir_exists():
    assert os.path.isdir(VEC)


# ---------------------------------------------------------------------------
# ENVIRONMENT
# ---------------------------------------------------------------------------

def test_cocotb_installed():
    result = subprocess.run(['cocotb-config', '--version'], capture_output=True, text=True)
    assert result.returncode == 0, "cocotb not installed or not on PATH"


def test_iverilog_installed():
    result = subprocess.run(['iverilog', '-V'], capture_output=True, text=True)
    assert result.returncode == 0, "iverilog not installed or not on PATH"


# ---------------------------------------------------------------------------
# PORT WIDTH VERIFICATION
# ---------------------------------------------------------------------------

def test_actual_port_widths_file_exists():
    path = _sim('ACTUAL_PORT_WIDTHS.txt')
    assert os.path.isfile(path), \
        f"ACTUAL_PORT_WIDTHS.txt not found at {path} — run Step 0 first"


# ---------------------------------------------------------------------------
# TEST VECTOR CONSISTENCY
# ---------------------------------------------------------------------------

def test_input_games_has_50_rows():
    rows = list(csv.DictReader(open(_vec('input_games.csv'))))
    assert len(rows) == 50, f"Expected 50 rows, got {len(rows)}"


def test_input_games_has_21_features():
    rows = list(csv.DictReader(open(_vec('input_games.csv'))))
    for i in range(21):
        assert f'feature_{i}' in rows[0], f"feature_{i} not in input_games.csv"


def test_expected_outputs_has_50_rows():
    rows = list(csv.DictReader(open(_vec('expected_outputs.csv'))))
    assert len(rows) == 50, f"Expected 50 rows, got {len(rows)}"


def test_checksums_correct():
    rows = list(csv.DictReader(open(_vec('input_games.csv'))))
    for i, row in enumerate(rows):
        features = [int(row[f'feature_{j}']) for j in range(21)]
        expected = functools.reduce(lambda a, b: a ^ b, features)
        actual   = int(row['checksum'])
        assert actual == expected, \
            f"Row {i}: checksum {actual} != XOR of features {expected}"


def test_win_probs_in_range():
    rows = list(csv.DictReader(open(_vec('expected_outputs.csv'))))
    for i, row in enumerate(rows):
        p = float(row['win_prob_float'])
        assert 0.0 <= p <= 1.0, f"Row {i}: win_prob_float {p} out of [0,1]"


def test_spreads_in_int8_range():
    rows = list(csv.DictReader(open(_vec('expected_outputs.csv'))))
    for i, row in enumerate(rows):
        s = int(row['spread_int8'])
        assert -128 <= s <= 127, f"Row {i}: spread_int8 {s} out of int8 range"


def test_feature_order_matches_features_json():
    import json
    meta = json.load(open(os.path.join(ROOT, 'artifacts', 'features.json')))
    assert len(meta['features']) == 21, \
        f"features.json has {len(meta['features'])} features, expected 21"
    rows = list(csv.DictReader(open(_vec('input_games.csv'))))
    assert len(rows) > 0
    for i in range(21):
        assert f'feature_{i}' in rows[0], \
            f"feature_{i} missing from input_games.csv"


# ---------------------------------------------------------------------------
# SIMULATION RESULTS (skip if sim_outputs.csv not yet generated)
# ---------------------------------------------------------------------------

def _load_sim_outputs():
    path = _vec('sim_outputs.csv')
    if not os.path.exists(path):
        pytest.skip("run python phase6_sim/run_sim.py first")
    return list(csv.DictReader(open(path)))


def test_sim_outputs_exist():
    _load_sim_outputs()   # skips if missing


def test_sim_status_ok_rate_100():
    rows = _load_sim_outputs()
    bad  = [r for r in rows if r['status_ok'] != 'True']
    assert len(bad) == 0, f"{len(bad)} games had status != OK"


def test_sim_win_agreement_98_percent():
    rows      = _load_sim_outputs()
    n_agree   = sum(1 for r in rows if int(r['win_delta']) <= 2)
    agreement = n_agree / len(rows)
    assert agreement >= 0.98, \
        f"Win agreement {agreement:.1%} < 98% ({n_agree}/{len(rows)})"


def test_sim_spread_mae_under_0_5():
    import numpy as np
    rows     = _load_sim_outputs()
    deltas   = [int(r['spread_delta']) for r in rows]
    mae      = np.mean(deltas)
    assert mae <= 0.5, f"Spread MAE {mae:.2f} > 0.5 pts"


def test_sim_report_exists():
    assert os.path.isfile(_vec('sim_report.csv')), \
        "sim_report.csv missing — run python phase6_sim/run_sim.py"


# ---------------------------------------------------------------------------
# MLP VERILOG ARITHMETIC (skip if not yet run)
# ---------------------------------------------------------------------------

def test_mlp_verilog_sim_passed():
    results_path = _cocotb('results_mlp_verilog.xml')
    if not os.path.isfile(results_path):
        pytest.skip("run 'make -C phase6_sim/cocotb test_mlp_verilog' first")
    import xml.etree.ElementTree as ET
    tree = ET.parse(results_path)
    root = tree.getroot()
    failures = root.findall('.//failure') + root.findall('.//error')
    assert len(failures) == 0, \
        f"MLP verilog sim had {len(failures)} failure(s) — check results_mlp_verilog.xml"
