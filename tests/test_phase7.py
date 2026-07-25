import csv
import functools
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from mlp.phase7_deploy.inference.fpga_client import FPGAClient
from mlp.phase7_deploy.inference.feature_builder import FeatureBuilder

BOARD_REQUIRED = pytest.mark.skipif(
    not os.environ.get('FPGA_PORT'),
    reason="Set FPGA_PORT=COM3 (or /dev/ttyUSB0) to run board tests",
)

FUNC = 'mlp/phase6_sim/functional'   # the authoritative real-IP vectors (post-audit cleanup)


# ── FILE EXISTENCE ──────────────────────────────────────────────────────────

def test_fpga_client_exists():
    assert Path('mlp/phase7_deploy/inference/fpga_client.py').exists()

def test_feature_builder_exists():
    assert Path('mlp/phase7_deploy/inference/feature_builder.py').exists()

def test_app_exists():
    assert Path('mlp/phase7_deploy/ui/app.py').exists()

def test_golden_vector_test_exists():
    assert Path('mlp/phase7_deploy/validation/golden_vector_test.py').exists()

def test_program_board_exists():
    assert Path('mlp/phase7_deploy/board/program_board.py').exists()


# ── HOST DECODE / ENCODE (no board — mocked serial) ─────────────────────────
# Replaces the old tautological decode asserts (audit §7.2): drive a real FPGAClient
# with a fake serial port and verify it interprets the board's 4-byte response correctly.

class FakeSerial:
    """Minimal stand-in for serial.Serial: returns a canned response, records writes."""
    def __init__(self, response: bytes):
        self.response = response
        self.is_open  = True
        self.written  = b''
    def reset_input_buffer(self):  pass
    def reset_output_buffer(self): pass
    def write(self, data):         self.written = bytes(data)
    def read(self, n):             return self.response[:n]
    def close(self):               self.is_open = False


def _client_with(resp: bytes) -> FPGAClient:
    c = FPGAClient('FAKE')
    c.ser = FakeSerial(resp)
    return c


def test_decode_ok():
    r = _client_with(bytes([0x55, 0x80, 0x03, 0x00])).run_inference([0] * 21)
    assert abs(r['win_prob'] - 0.5) < 1e-9
    assert r['spread'] == 3
    assert r['status'] == 'OK'
    assert r['raw_win'] == 0x80 and r['raw_spread'] == 0x03

def test_decode_negative_spread():
    # spread byte 0xFB = -5 as signed int8
    r = _client_with(bytes([0x55, 0x00, 0xFB, 0x00])).run_inference([0] * 21)
    assert r['spread'] == -5

def test_decode_win_extremes():
    assert _client_with(bytes([0x55, 0x00, 0x00, 0x00])).run_inference([0]*21)['win_prob'] == 0.0
    assert _client_with(bytes([0x55, 0xFF, 0x00, 0x00])).run_inference([0]*21)['win_prob'] == 255/256.0

def test_decode_nack_status():
    assert _client_with(bytes([0x55, 0x00, 0x00, 0x01])).run_inference([0]*21)['status'] == 'NACK'

def test_decode_timeout_status():          # G1: the watchdog status the HDL now emits
    assert _client_with(bytes([0x55, 0x00, 0x00, 0x02])).run_inference([0]*21)['status'] == 'TIMEOUT'

def test_decode_unknown_status():
    assert 'UNKNOWN' in _client_with(bytes([0x55, 0x00, 0x00, 0x7F])).run_inference([0]*21)['status']

def test_decode_short_read_raises_timeout():
    with pytest.raises(TimeoutError):
        _client_with(bytes([0x55, 0x00])).run_inference([0] * 21)

def test_decode_bad_sof_raises():
    with pytest.raises(RuntimeError):
        _client_with(bytes([0xAA, 0x00, 0x00, 0x00])).run_inference([0] * 21)

def test_run_inference_writes_correct_packet():
    feats = list(range(21))
    c = _client_with(bytes([0x55, 0x80, 0x03, 0x00]))
    c.run_inference(feats)
    expected = bytes([0xAA] + feats + [functools.reduce(lambda a, b: a ^ b, feats)])
    assert c.ser.written == expected

def test_feature_count_validation():
    with pytest.raises(ValueError):
        _client_with(bytes([0x55, 0, 0, 0])).run_inference([0] * 20)     # wrong count
    with pytest.raises(ValueError):
        _client_with(bytes([0x55, 0, 0, 0])).run_inference([300] + [0]*20)  # byte out of range

def test_checksum_computation():
    assert FPGAClient.compute_checksum([0] * 21) == 0
    assert FPGAClient.compute_checksum([0xFF] * 21) == 0xFF      # XOR of 21 (odd) identical bytes
    assert FPGAClient.compute_checksum([1, 2, 3] + [0] * 18) == 1 ^ 2 ^ 3


# ── ENCODE CONSISTENCY (host == sim/golden == what the hardware expects) ─────

def test_host_encoding_matches_sim_vectors():
    """FeatureBuilder._encode_row must produce the exact bytes the sim/golden used.
    Both use clip(round(scaler.transform(x) * 255)); this pins them together so the
    board receives precisely what we verified in simulation."""
    import pandas as pd
    builder = FeatureBuilder()
    games = pd.read_parquet('data/processed/games.parquet')
    row0  = games[games['season'].isin([2023, 2024])].head(1).iloc[0]   # == gen_vectors game 0
    host_bytes = builder._encode_row(row0)
    sim_bytes  = [int(x, 16) for x in open(f'{FUNC}/tb_inputs.mem').readline().split()]
    assert host_bytes == sim_bytes


# ── FEATURE BUILDER (no board) ──────────────────────────────────────────────

def test_feature_count():
    feats, _ = FeatureBuilder().from_historical_game(2024, 1, 'KC')
    assert len(feats) == 21 and all(0 <= b <= 255 for b in feats)

def test_feature_builder_loads():
    builder = FeatureBuilder()
    assert len(builder.features) == 21
    assert 'KC' in builder.get_available_teams() or 'KAN' in builder.get_available_teams()

def test_historical_game_not_found_raises():
    with pytest.raises(ValueError):
        FeatureBuilder().from_historical_game(2024, 1, 'INVALID_TEAM')

def test_available_seasons_includes_recent():
    seasons = FeatureBuilder().get_available_seasons()
    assert 2024 in seasons and 2023 in seasons


# ── VECTORS PRESENT ─────────────────────────────────────────────────────────

def test_functional_vectors_exist():
    assert os.path.isfile(f'{FUNC}/golden.csv')
    assert os.path.isfile(f'{FUNC}/tb_inputs.mem')
    assert len(list(csv.DictReader(open(f'{FUNC}/golden.csv')))) == 50


# ── BOARD TESTS (require FPGA_PORT) ─────────────────────────────────────────

@BOARD_REQUIRED
def test_board_uart_connects():
    client = FPGAClient(os.environ['FPGA_PORT'])
    client.connect()
    assert client.is_connected()
    client.disconnect()

@BOARD_REQUIRED
def test_board_smoke_test():
    feats, _ = FeatureBuilder().from_historical_game(2023, 1, 'KC')
    with FPGAClient(os.environ['FPGA_PORT']) as client:
        result = client.run_inference(feats)
    assert result['status'] == 'OK'
    assert 0.0 <= result['win_prob'] <= 1.0
    assert -50 <= result['spread'] <= 50
    assert result['latency_ms'] < 100

@BOARD_REQUIRED
def test_board_checksum_nack():
    import serial
    features     = [128] * 21
    bad_checksum = functools.reduce(lambda a, b: a ^ b, features) ^ 0xFF
    packet       = bytes([0xAA] + features + [bad_checksum])
    with serial.Serial(os.environ['FPGA_PORT'], 115200, timeout=2) as ser:
        ser.reset_input_buffer()
        ser.write(packet)
        resp = ser.read(4)
    assert len(resp) == 4 and resp[0] == 0x55 and resp[3] == 0x01

@BOARD_REQUIRED
def test_board_golden_vectors():
    feats_all = [[int(x, 16) for x in line.split()]
                 for line in open(f'{FUNC}/tb_inputs.mem').read().splitlines()]
    golden    = list(csv.DictReader(open(f'{FUNC}/golden.csv')))
    passed = 0
    with FPGAClient(os.environ['FPGA_PORT']) as client:
        for feats, g in list(zip(feats_all, golden))[:20]:
            r = client.run_inference(feats)
            if r['status'] == 'OK' and abs(r['raw_win'] - int(g['win_byte'])) <= 26:
                passed += 1
    assert passed >= 18, f"Only {passed}/20 golden vectors passed on board"
