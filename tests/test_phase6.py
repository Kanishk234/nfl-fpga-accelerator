# tests/test_phase6.py — Phase 6 pytest suite (post-audit; io_stream + real-IP functional suite).
#
# Rewritten after the cleanup that removed the io_serial-era cocotb regression/stubs/test_vectors.
# Verifies: the functional suite is present, the real IP is installed, the environment is sane,
# the synthesis sign-off numbers pass, and (if a sim was run) the real-IP regression results meet
# the same criteria as mlp/phase6_sim/functional/check_results.py.

import os
import csv
import glob
import json
import subprocess
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
FUNC = os.path.join(ROOT, 'mlp/phase6_sim', 'functional')
COCOTB = os.path.join(ROOT, 'mlp/phase6_sim', 'cocotb')
IPV  = os.path.join(ROOT, 'artifacts', 'mlp', 'ip_repo', 'hdl', 'verilog')

COINFLIP_BAND = 0.05    # excuse winner flips on games the model scores near 0.5
WIN_MAX_TOL   = 26      # counts (~0.10 prob) — fixed<18,6> C-sim envelope
SPREAD_MAX_TOL = 3      # points


# ── FILE EXISTENCE: functional suite (authoritative) ────────────────────────

@pytest.mark.parametrize('name', [
    'gen_vectors.py', 'tb_regression_real.v', 'tb_top_uart.v', 'tb_timeout.v',
    'tb_top_timeout.v', 'tb_framing_resync.v', 'run_xsim.bat', 'run_xsim_uart.bat',
    'check_results.py', 'myproject_stub_stall.v', 'golden.csv', 'tb_inputs.mem', 'ABOUT.md',
])
def test_functional_file_exists(name):
    assert os.path.isfile(os.path.join(FUNC, name)), f"{name} missing from mlp/phase6_sim/functional/"


# ── FILE EXISTENCE: cocotb UART unit tests (kept) ───────────────────────────

@pytest.mark.parametrize('name', ['uart_helpers.py', 'test_uart_rx.py', 'test_uart_tx.py',
                                  'test_uart_framing.py', 'Makefile'])
def test_cocotb_uart_file_exists(name):
    assert os.path.isfile(os.path.join(COCOTB, name))


def test_obsolete_io_serial_artifacts_removed():
    # the stubs that masked the original deadlock must stay gone
    assert not os.path.isdir(os.path.join(ROOT, 'mlp/phase6_sim', 'stubs'))
    assert not os.path.isfile(os.path.join(COCOTB, 'test_regression.py'))


def test_real_ip_installed():
    vfiles = glob.glob(os.path.join(IPV, '*.v'))
    assert len(vfiles) >= 30, f"Expected >=30 IP Verilog files in {IPV}, found {len(vfiles)}"
    # must be the io_stream IP, not the old ap_memory one
    with open(os.path.join(IPV, 'myproject.v')) as f:
        assert 'features_TDATA' in f.read(), "ip_repo is not the io_stream IP (no features_TDATA)"


# ── ENVIRONMENT ─────────────────────────────────────────────────────────────

def test_iverilog_installed():
    assert subprocess.run(['iverilog', '-V'], capture_output=True).returncode == 0


def test_cocotb_installed():
    assert subprocess.run(['cocotb-config', '--version'], capture_output=True).returncode == 0


# ── VECTOR CONSISTENCY ──────────────────────────────────────────────────────

def test_golden_has_50_rows():
    rows = list(csv.DictReader(open(os.path.join(FUNC, 'golden.csv'))))
    assert len(rows) == 50


def test_tb_inputs_50_games_21_bytes():
    lines = open(os.path.join(FUNC, 'tb_inputs.mem')).read().splitlines()
    assert len(lines) == 50
    for i, line in enumerate(lines):
        assert len(line.split()) == 21, f"game {i} does not have 21 feature bytes"


def test_feature_count_matches_features_json():
    meta = json.load(open(os.path.join(ROOT, 'artifacts', 'features.json')))
    assert len(meta['features']) == 21


# ── SYNTHESIS SIGN-OFF (from artifacts/mlp/synthesis_report.json) ───────────────

def test_synthesis_resources_within_budget():
    rep = json.load(open(os.path.join(ROOT, 'artifacts', 'mlp', 'synthesis_report.json')))
    for name, r in rep['resources'].items():
        assert r['ok'], f"{name} over budget: {r['used']}/{r.get('budget', r['total'])}"
    assert rep['resources']['LUT']['used'] <= 20800


def test_timing_closes_at_100mhz():
    rep = json.load(open(os.path.join(ROOT, 'artifacts', 'mlp', 'synthesis_report.json')))
    assert rep['timing']['timing_ok'] and rep['timing']['WNS_ns'] >= 0.0


# ── REAL-IP FUNCTIONAL REGRESSION (skip if sim not run) ─────────────────────

def _s8(b):
    b = int(b); return b - 256 if b > 127 else b


def _load_regression():
    sim_path = os.path.join(FUNC, 'sim_results.csv')
    if not os.path.isfile(sim_path):
        pytest.skip("run mlp/phase6_sim/functional/run_xsim.bat to produce sim_results.csv")
    golden = {int(r['game_idx']): r for r in csv.DictReader(open(os.path.join(FUNC, 'golden.csv')))}
    sim    = {int(r['game_idx']): r for r in csv.DictReader(open(sim_path))}
    return golden, sim


def test_regression_no_timeouts():
    golden, sim = _load_regression()
    assert all(int(s['timeout']) == 0 for s in sim.values()), "some games hit the IP watchdog"


def test_regression_confident_winner_agreement():
    golden, sim = _load_regression()
    for idx, g in golden.items():
        if idx not in sim or int(sim[idx]['timeout']):
            continue
        p = float(g['win_prob'])
        if abs(p - 0.5) >= COINFLIP_BAND:   # confident game -> winner must match
            assert (int(sim[idx]['win_byte']) >= 128) == (p >= 0.5), \
                f"game {idx}: confident-game winner flip (model {p:.3f})"


def test_regression_deltas_within_envelope():
    golden, sim = _load_regression()
    win_max = spread_max = 0
    for idx, g in golden.items():
        if idx not in sim or int(sim[idx]['timeout']):
            continue
        win_max    = max(win_max, abs(int(sim[idx]['win_byte']) - int(g['win_byte'])))
        spread_max = max(spread_max, abs(_s8(sim[idx]['spread_byte']) - int(g['spread_byte'])))
    assert win_max <= WIN_MAX_TOL, f"win delta max {win_max} > {WIN_MAX_TOL}"
    assert spread_max <= SPREAD_MAX_TOL, f"spread delta max {spread_max} > {SPREAD_MAX_TOL}"
