"""Phase 6 pytest suite — file existence, reference vector consistency, cocotb sim results."""
import os
import json
import subprocess
import functools
import xml.etree.ElementTree as ET
import pytest


# ---------------------------------------------------------------------------
# FILE EXISTENCE TESTS
# ---------------------------------------------------------------------------

def test_cocotb_makefile_exists():
    assert os.path.isfile('phase6_sim/cocotb/Makefile'), \
        "phase6_sim/cocotb/Makefile not found"


def test_test_uart_rx_exists():
    assert os.path.isfile('phase6_sim/cocotb/test_uart_rx.py'), \
        "phase6_sim/cocotb/test_uart_rx.py not found"


def test_test_uart_tx_exists():
    assert os.path.isfile('phase6_sim/cocotb/test_uart_tx.py'), \
        "phase6_sim/cocotb/test_uart_tx.py not found"


def test_test_uart_framing_exists():
    assert os.path.isfile('phase6_sim/cocotb/test_uart_framing.py'), \
        "phase6_sim/cocotb/test_uart_framing.py not found"


def test_test_mlp_controller_exists():
    assert os.path.isfile('phase6_sim/cocotb/test_mlp_controller.py'), \
        "phase6_sim/cocotb/test_mlp_controller.py not found"


def test_test_top_integration_exists():
    assert os.path.isfile('phase6_sim/cocotb/test_top_integration.py'), \
        "phase6_sim/cocotb/test_top_integration.py not found"


def test_reference_vectors_exist():
    assert os.path.isfile('phase6_sim/reference/golden_vectors.json'), \
        "phase6_sim/reference/golden_vectors.json not found — run generate_vectors.py"


def test_fast_stub_exists():
    hdl_dir = 'phase5_fpga/hdl'
    stubs = [f for f in os.listdir(hdl_dir)
             if 'stub' in f.lower() and f.endswith('.v') and 'fast' in f.lower()]
    assert len(stubs) > 0, \
        "No fast stub .v file found in phase5_fpga/hdl/ — expected myproject_stub_fast.v"


# ---------------------------------------------------------------------------
# REFERENCE VECTOR CONSISTENCY TESTS
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def golden_vectors():
    with open('phase6_sim/reference/golden_vectors.json') as f:
        return json.load(f)


def test_golden_vectors_have_correct_count(golden_vectors):
    assert len(golden_vectors) == 10, \
        f"Expected 10 vectors, got {len(golden_vectors)}"


def test_golden_vectors_feature_count(golden_vectors):
    for v in golden_vectors:
        assert len(v['features_uint8']) == 21, \
            f"Vector {v['game_index']}: features_uint8 has {len(v['features_uint8'])} entries"
        assert len(v['features_float']) == 21, \
            f"Vector {v['game_index']}: features_float has {len(v['features_float'])} entries"


def test_golden_vectors_checksum_correct(golden_vectors):
    for v in golden_vectors:
        computed = functools.reduce(lambda a, b: a ^ b, v['features_uint8'])
        assert computed == v['checksum'], \
            f"Vector {v['game_index']}: checksum mismatch — computed {computed}, stored {v['checksum']}"


def test_golden_vectors_features_in_uint8_range(golden_vectors):
    for v in golden_vectors:
        for i, b in enumerate(v['features_uint8']):
            assert 0 <= b <= 255, \
                f"Vector {v['game_index']} feature[{i}] = {b} out of uint8 range"


def test_golden_vectors_win_prob_in_range(golden_vectors):
    for v in golden_vectors:
        assert 0.0 <= v['win_prob_float'] <= 1.0, \
            f"Vector {v['game_index']}: win_prob_float={v['win_prob_float']} out of [0,1]"
        assert 0 <= v['win_uint8_expected'] <= 255, \
            f"Vector {v['game_index']}: win_uint8_expected={v['win_uint8_expected']} out of [0,255]"


def test_golden_vectors_spread_in_int8_range(golden_vectors):
    for v in golden_vectors:
        assert -128 <= v['spread_int8_expected'] <= 127, \
            f"Vector {v['game_index']}: spread_int8_expected={v['spread_int8_expected']} out of int8 range"


def test_feature_order_matches_features_json():
    """Re-derive vector 0 from scratch and compare to stored features_uint8."""
    import pickle
    import numpy as np
    import pandas as pd

    meta    = json.load(open('artifacts/features.json'))
    vectors = json.load(open('phase6_sim/reference/golden_vectors.json'))
    scaler  = pickle.load(open('artifacts/scaler.pkl', 'rb'))
    games   = pd.read_parquet('data/processed/games.parquet')

    sample   = games[games['season'] == 2024].head(1)
    X        = scaler.transform(sample[meta['features']].values.astype('float32'))
    expected = np.round(X[0] * 255).astype(int).tolist()

    assert expected == vectors[0]['features_uint8'], \
        "Feature order or scaler mismatch between golden vectors and artifacts"


# ---------------------------------------------------------------------------
# SIMULATION ENVIRONMENT TESTS
# ---------------------------------------------------------------------------

def test_cocotb_installed():
    result = subprocess.run(
        ['python', '-c', 'import cocotb; print(cocotb.__version__)'],
        capture_output=True, text=True
    )
    assert result.returncode == 0, \
        f"cocotb not installed — pip install cocotb\n{result.stderr}"


def test_iverilog_installed():
    result = subprocess.run(['which', 'iverilog'], capture_output=True)
    assert result.returncode == 0, \
        "iverilog not installed — sudo apt install iverilog"


def test_cocotb_makefiles_available():
    result = subprocess.run(
        ['cocotb-config', '--makefiles'],
        capture_output=True, text=True
    )
    assert result.returncode == 0, "cocotb-config --makefiles failed"
    makefiles_dir = result.stdout.strip()
    assert os.path.isdir(makefiles_dir), \
        f"cocotb makefiles directory not found: {makefiles_dir}"


# ---------------------------------------------------------------------------
# COCOTB SIMULATION RESULT TESTS
# (skip gracefully if simulation has not yet been run)
# ---------------------------------------------------------------------------

def _check_sim_results(results_xml):
    if not os.path.isfile(results_xml):
        pytest.skip(f"Simulation not yet run — results file missing: {results_xml}")
    tree     = ET.parse(results_xml)
    failures = tree.findall('.//failure') + tree.findall('.//error')
    if failures:
        msgs = [f.get('message', f.text or 'no message') for f in failures]
        pytest.fail(f"Simulation failures in {results_xml}:\n" + "\n".join(msgs))


def test_uart_rx_sim_passed():
    _check_sim_results('phase6_sim/cocotb/results_uart_rx.xml')


def test_uart_tx_sim_passed():
    _check_sim_results('phase6_sim/cocotb/results_uart_tx.xml')


def test_uart_framing_sim_passed():
    _check_sim_results('phase6_sim/cocotb/results_uart_framing.xml')


def test_mlp_controller_sim_passed():
    _check_sim_results('phase6_sim/cocotb/results_mlp_controller.xml')


def test_integration_sim_passed():
    _check_sim_results('phase6_sim/cocotb/results_integration.xml')
