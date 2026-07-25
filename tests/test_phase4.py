"""
Phase 4 test suite — 19 tests.

Assumes convert.py has been run (HLS project generated + csim complete).
Synthesis-dependent tests (resource_report, timing, IP export) require
resource_report.py to have been run after Windows Vitis HLS synthesis.
"""

import os
import sys
import glob
import json
import shutil
import pickle

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HLS_DIR = 'mlp/phase4_hls/hls_project'
AMD_VITIS_PATH = '/mnt/c/AMDDesignTools/2025.2/Vitis/scripts/vitis_hls'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def artifacts():
    import tensorflow as tf
    tf.config.run_functions_eagerly(True)
    from mlp.phase3_quantization.qkeras_model import build_quantized_model, compile_quantized_model

    meta     = json.load(open('artifacts/features.json'))
    features = meta['features']
    scaler   = pickle.load(open('artifacts/scaler.pkl', 'rb'))
    games    = pd.read_parquet('data/processed/games.parquet')
    val      = games[games['season'].isin([2021, 2022])]
    X_val    = scaler.transform(val[features].values.astype('float32'))
    y_val    = val['home_win'].values

    model = build_quantized_model(n_features=len(features))
    compile_quantized_model(model)
    model.load_weights('artifacts/mlp/model_quantized.keras')

    return {
        'model':    model,
        'X_val':    X_val,
        'y_val':    y_val,
        'features': features,
        'meta':     meta,
    }


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

class TestEnvironment:
    def test_environment_vivado_accessible(self):
        """Vitis HLS is accessible — either on WSL PATH or at the AMD Windows install path."""
        on_path      = shutil.which('vitis_hls') is not None
        at_amd_path  = os.path.exists(AMD_VITIS_PATH)
        assert on_path or at_amd_path, (
            "Vitis HLS not found on PATH and not found at AMD install path.\n"
            f"  Expected at: {AMD_VITIS_PATH}\n"
            "  If Vivado is installed elsewhere, update AMD_VITIS_PATH in this file."
        )


# ---------------------------------------------------------------------------
# HLS project structure
# ---------------------------------------------------------------------------

class TestHLSProjectStructure:
    def test_hls_project_exists(self):
        assert os.path.isdir(HLS_DIR), f"HLS project dir missing: {HLS_DIR} — run convert.py"

    def test_hls_project_has_firmware_dir(self):
        assert os.path.isdir(os.path.join(HLS_DIR, 'firmware'))

    def test_firmware_cpp_exists(self):
        assert os.path.isfile(os.path.join(HLS_DIR, 'firmware/myproject.cpp'))

    def test_firmware_h_exists(self):
        assert os.path.isfile(os.path.join(HLS_DIR, 'firmware/myproject.h'))

    def test_weights_dir_exists(self):
        assert os.path.isdir(os.path.join(HLS_DIR, 'firmware/weights'))

    def test_weight_files_generated(self):
        weights_dir = os.path.join(HLS_DIR, 'firmware/weights')
        n = len(os.listdir(weights_dir))
        assert n >= 8, f"Expected >=8 weight header files, got {n}"


# ---------------------------------------------------------------------------
# Generated C++ content
# ---------------------------------------------------------------------------

class TestGeneratedCPP:
    @pytest.fixture(autouse=True)
    def cpp_content(self):
        path = os.path.join(HLS_DIR, 'firmware/myproject.cpp')
        with open(path) as f:
            self._content = f.read()

    def test_cpp_contains_all_layers(self):
        for layer in ('dense_1', 'dense_2', 'dense_3'):
            assert layer in self._content, f"{layer} not found in myproject.cpp"

    def test_cpp_uses_ap_fixed(self):
        # Vitis backend uses typedef names (input_t, layer2_t) in myproject.cpp;
        # the ap_fixed definitions live in defines.h — check both.
        src = self._content
        defines_path = os.path.join(HLS_DIR, 'firmware/defines.h')
        if os.path.isfile(defines_path):
            with open(defines_path) as f:
                src = src + f.read()
        assert 'ap_fixed' in src

    def test_cpp_uses_nnet_functions(self):
        assert 'nnet' in self._content


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

class TestArtifacts:
    def test_hls_config_saved(self):
        assert os.path.isfile('artifacts/mlp/hls_config.json')
        config = json.load(open('artifacts/mlp/hls_config.json'))
        rf = config['reuse_factor']
        # rf is a per-layer dict; check hidden dense layers use meaningful reuse (>=147 each)
        dense_rfs = [rf[k] for k in ('dense_1', 'dense_2', 'dense_3')]
        assert min(dense_rfs) >= 147, f"Dense layer RF too low: {rf}"
        assert config['part'] == 'xc7a35tcpg236-1'
        assert config['backend'] == 'Vitis'

    def test_feature_count_unchanged(self):
        meta = json.load(open('artifacts/features.json'))
        assert len(meta['features']) == 21


# ---------------------------------------------------------------------------
# C simulation results
# ---------------------------------------------------------------------------

class TestCSimResults:
    @pytest.fixture(autouse=True)
    def report(self):
        assert os.path.isfile('artifacts/mlp/hls_resource_report.json'), \
            "hls_resource_report.json missing — run convert.py"
        self._report = json.load(open('artifacts/mlp/hls_resource_report.json'))

    def test_csim_mean_delta_within_tolerance(self):
        delta = self._report.get('csim_mean_delta')
        assert delta is not None, "csim_mean_delta missing — re-run convert.py"
        assert delta <= 0.05, f"Mean delta {delta:.4f} exceeds 0.05 tolerance"

    def test_csim_max_delta_within_tolerance(self):
        delta = self._report.get('csim_max_delta')
        assert delta is not None, "csim_max_delta missing — re-run convert.py"
        assert delta <= 0.10, f"Max delta {delta:.4f} exceeds 0.10 tolerance"


# ---------------------------------------------------------------------------
# Synthesis results (require resource_report.py to have been run)
# ---------------------------------------------------------------------------

class TestSynthesisResults:
    @pytest.fixture(autouse=True)
    def report(self):
        assert os.path.isfile('artifacts/mlp/hls_resource_report.json'), \
            "hls_resource_report.json missing — run resource_report.py"
        self._report = json.load(open('artifacts/mlp/hls_resource_report.json'))
        if 'resources' not in self._report:
            pytest.skip("Synthesis not yet run — open project in Windows Vitis HLS, "
                        "run C Synthesis + Export, then run resource_report.py")

    def test_resource_report_saved(self):
        assert 'resources' in self._report
        assert 'basys3_fit' in self._report

    def test_resource_report_fits_basys3(self):
        assert self._report['basys3_fit'] is True, \
            "Design exceeds Basys 3 budget — increase reuse_factor in convert.py"

    def test_dsp_count_within_budget(self):
        assert self._report['resources']['DSP'] <= 80, \
            "DSP count exceeds budget (80) — increase reuse_factor"

    def test_bram_within_budget(self):
        assert self._report['resources']['BRAM_18K'] <= 80

    def test_timing_met(self):
        assert self._report.get('timing_met') is True, \
            "100 MHz timing not met — set clock_period=20 in convert.py and re-run"

    def test_ip_export_exists(self):
        ip_dirs = glob.glob(f'{HLS_DIR}/**/impl/ip', recursive=True)
        assert len(ip_dirs) > 0, \
            "IP export directory not found — re-run resource_report.py with export=True"
