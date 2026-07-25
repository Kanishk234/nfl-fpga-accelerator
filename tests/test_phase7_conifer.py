"""
Phase 7 (conifer) host-side tests.

Everything here runs without the board. The board tests at the bottom are gated
on FPGA_PORT, same convention as tests/test_phase7.py.

The load-bearing test is `test_host_encoding_matches_chain_golden`: it proves
the host produces byte-for-byte the same packet that XSIM verified. Without it
a board mismatch is unattributable — you cannot tell a host encoding bug from
an RTL bug.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from gbdt.phase7_deploy.inference.fpga_client_gbdt import (
    GBDTClient, SCALE, from_fixed, pack_features, to_fixed,
)

BOARD_REQUIRED = pytest.mark.skipif(
    not os.environ.get('FPGA_PORT'),
    reason="Set FPGA_PORT=COM8 to run board tests",
)

CONIFER = Path('gbdt/phase7_deploy')
CHAIN = Path('gbdt/phase6_sim/chain_golden')
VECTORS = CONIFER / 'validation_vectors.json'
CATALOG = CONIFER / 'games_catalog_gbdt.json'


# ── FILES ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('rel', [
    'inference/fpga_client_gbdt.py',
    'inference/feature_builder_gbdt.py',
    'export_catalog_gbdt.py',
    'board/verify_uart_gbdt.py',
    'validation/golden_vector_test_gbdt.py',
    'ui/webapp_gbdt.py',
])
def test_deliverable_exists(rel):
    assert (CONIFER / rel).exists(), f"missing {rel}"


# ── FIXED-POINT CONVERSION ──────────────────────────────────────────────────

def test_to_fixed_matches_scale():
    assert to_fixed(1.0) == SCALE
    assert to_fixed(0.0) == 0

def test_to_fixed_negative_is_twos_complement():
    assert to_fixed(-1.0) == (1 << 24) - SCALE
    assert from_fixed(to_fixed(-1.0)) == -1.0

def test_fixed_roundtrip_on_grid():
    for v in (0.0, 0.5, -0.5, 1523.25, -7.125, 2047.75):
        assert from_fixed(to_fixed(v)) == pytest.approx(v)

def test_to_fixed_rejects_out_of_range():
    # ap_fixed<24,12> holds [-2048, +2048); the hardware has the same limit.
    with pytest.raises(ValueError):
        to_fixed(2048.0)
    with pytest.raises(ValueError):
        to_fixed(-2049.0)

def test_win_word_decodes_unsigned():
    # win_prob is always in [0,1) from the sigmoid ROM — never sign-extend it.
    assert from_fixed(0xFFF, signed=False) == pytest.approx(4095 / 4096)


# ── PACKET CONSTRUCTION ─────────────────────────────────────────────────────

def test_pack_features_is_lsb_first():
    assert pack_features([0x123456] + [0] * 20)[:3] == [0x56, 0x34, 0x12]

def test_pack_features_length():
    assert len(pack_features([0] * 21)) == 63

def test_pack_features_rejects_wrong_count():
    with pytest.raises(ValueError):
        pack_features([0] * 20)

def test_checksum_is_xor_of_feature_bytes():
    b = list(range(63))
    x = 0
    for v in b:
        x ^= v
    assert GBDTClient.compute_checksum(b) == x

def test_sof_collision_bytes_are_data_not_reframed():
    # 63 bytes of 0xAA XOR to 0xAA (odd count) — the phase-6 SOF-collision case.
    assert GBDTClient.compute_checksum([0xAA] * 63) == 0xAA


# ── HOST DECODE (mocked serial, no board) ───────────────────────────────────

class FakeSerial:
    """Minimal stand-in for serial.Serial: canned response, records writes."""

    def __init__(self, response: bytes):
        self.response = response
        self.is_open = True
        self.written = b''

    def reset_input_buffer(self): pass
    def reset_output_buffer(self): pass
    def write(self, data): self.written = bytes(data)
    def read(self, n): return self.response[:n]
    def close(self): self.is_open = False


def _client_with(resp: bytes) -> GBDTClient:
    c = GBDTClient('FAKE')
    c.ser = FakeSerial(resp)
    return c


def _response(win: int, spread: int, status: int = 0x00) -> bytes:
    return bytes([0x55,
                  win & 0xFF, (win >> 8) & 0xFF, (win >> 16) & 0xFF,
                  spread & 0xFF, (spread >> 8) & 0xFF, (spread >> 16) & 0xFF,
                  status])


def test_client_sends_65_byte_framed_packet():
    c = _client_with(_response(2048, 0))
    feats = list(range(63))
    c.run_inference(feats)
    sent = c.ser.written
    assert len(sent) == 65
    assert sent[0] == 0xAA
    assert list(sent[1:64]) == feats
    assert sent[64] == GBDTClient.compute_checksum(feats)


def test_client_decodes_win_and_spread():
    r = _client_with(_response(to_fixed(0.75), to_fixed(-3.5))).run_inference([0] * 63)
    assert r['win_prob'] == pytest.approx(0.75)
    assert r['spread'] == pytest.approx(-3.5)
    assert r['status'] == 'OK'


def test_client_reports_nack():
    assert _client_with(_response(0, 0, 0x01)).run_inference([0] * 63)['status'] == 'NACK'


def test_client_reports_watchdog_timeout():
    assert _client_with(_response(0, 0, 0x02)).run_inference([0] * 63)['status'] == 'TIMEOUT'


def test_client_rejects_bad_sof():
    c = _client_with(b'\x99' + b'\x00' * 7)
    with pytest.raises(RuntimeError, match='SOF'):
        c.run_inference([0] * 63)


def test_client_raises_on_short_read():
    c = _client_with(b'\x55\x00\x00')
    with pytest.raises(TimeoutError):
        c.run_inference([0] * 63)


def test_client_rejects_wrong_feature_count():
    c = _client_with(_response(0, 0))
    with pytest.raises(ValueError):
        c.run_inference([0] * 21)


# ── VALIDATION BUNDLE / CATALOG ─────────────────────────────────────────────

@pytest.mark.skipif(not VECTORS.exists(), reason="run export_catalog_gbdt.py first")
def test_validation_vectors_wellformed():
    d = json.loads(VECTORS.read_text())
    assert d['n'] == 100
    for v in d['vectors']:
        assert len(v['bytes']) == 63
        assert all(0 <= b <= 255 for b in v['bytes'])
        assert 0 <= v['exp_win'] < (1 << 24)


@pytest.mark.skipif(not VECTORS.exists() or not CHAIN.exists(),
                    reason="needs the exported bundle + phase 6 golden")
def test_validation_vectors_match_chain_golden():
    """The bundle's inputs and expected outputs must equal the phase 6 files."""
    d = json.loads(VECTORS.read_text())
    words = [int(x, 16) for x in (CHAIN / 'tb_inputs.mem').read_text().split()]
    fixed = [int(x, 16) for x in (CHAIN / 'golden_fixed.mem').read_text().split()]

    for v in d['vectors']:
        g = v['game_idx']
        packed = pack_features(words[g * 21:(g + 1) * 21])
        assert v['bytes'] == packed, f"game {g} input bytes drifted"
        assert v['exp_win'] == fixed[g * 2]
        assert v['exp_spread'] == fixed[g * 2 + 1]


@pytest.mark.skipif(not VECTORS.exists(), reason="run export_catalog_gbdt.py first")
def test_xsim_agrees_with_golden():
    """Phase 6 claimed XSIM == golden bit-exactly; hold that claim to account."""
    d = json.loads(VECTORS.read_text())
    checked = [v for v in d['vectors'] if 'sim_win' in v]
    assert checked, "sim_results.csv was absent when the bundle was built"
    for v in checked:
        assert v['sim_win'] == v['exp_win'], f"game {v['game_idx']} win"
        assert v['sim_spread'] == v['exp_spread'], f"game {v['game_idx']} spread"


@pytest.mark.skipif(not CATALOG.exists(), reason="run export_catalog_gbdt.py first")
def test_catalog_wellformed():
    d = json.loads(CATALOG.read_text())
    assert d['model'] == 'gbdt'
    assert d['n_games'] > 6000
    for g in d['games'][:200]:
        assert len(g['bytes']) == 63


# ── THE LOAD-BEARING ONE: host encoding == what XSIM simulated ──────────────

@pytest.mark.skipif(not CHAIN.exists(), reason="phase 6 golden not present")
def test_host_encoding_matches_chain_golden():
    """Re-encode the 100 val games from the parquet and require an exact match
    against chain_golden/tb_inputs.mem.

    Guards the float32 cast in GBDTFeatureBuilder.quantize(): dropping it shifts
    the LSB on ~half the games, which would silently break board bit-exactness.
    """
    pytest.importorskip('pandas')
    from gbdt.phase7_deploy.inference.feature_builder_gbdt import GBDTFeatureBuilder

    b = GBDTFeatureBuilder()
    words = [int(x, 16) for x in (CHAIN / 'tb_inputs.mem').read_text().split()]
    n = len(words) // 21
    va = b.games[b.games['season'].isin([2021, 2022])]

    for g in range(n):
        assert b.fixed_words(va.iloc[g]) == words[g * 21:(g + 1) * 21], \
            f"game {g} encoding differs from the simulated vectors"


# ── UI WIRING ───────────────────────────────────────────────────────────────

def test_shared_index_is_model_driven():
    """Both UIs serve mlp/phase7_deploy/ui/index.html; it must read its labels from
    the bootstrap `model` block rather than hardcoding MLP strings."""
    html = Path('mlp/phase7_deploy/ui/index.html').read_text(encoding='utf-8')
    assert 'MODEL.steps' in html
    assert 'modelChip' in html
    assert 'const STEPS' not in html, "pipeline labels are hardcoded again"


def test_gbdt_ui_declares_its_model():
    sys.path.insert(0, str(Path('gbdt/phase7_deploy/ui').resolve()))
    src = (CONIFER / 'ui/webapp_gbdt.py').read_text(encoding='utf-8')
    assert "'name': 'GBDT'" in src
    assert "'raw_denom': 4096" in src


# ── BOARD TESTS (need real hardware) ────────────────────────────────────────

@BOARD_REQUIRED
def test_board_smoke():
    from gbdt.phase7_deploy.board.verify_uart_gbdt import smoke_test
    assert smoke_test(os.environ['FPGA_PORT'])


@BOARD_REQUIRED
def test_board_bit_exact_20_games():
    from gbdt.phase7_deploy.validation.golden_vector_test_gbdt import run
    assert run(os.environ['FPGA_PORT'], limit=20, verbose=False)


@BOARD_REQUIRED
def test_board_nacks_bad_checksum():
    """Corrupt the checksum on the wire and require status 0x01."""
    import serial
    v = json.loads(VECTORS.read_text())['vectors'][0]
    feats = v['bytes']
    bad = GBDTClient.compute_checksum(feats) ^ 0xFF
    with serial.Serial(os.environ['FPGA_PORT'], 115200, timeout=2.0) as s:
        s.reset_input_buffer()
        s.write(bytes([0xAA] + feats + [bad]))
        resp = s.read(8)
    assert len(resp) == 8 and resp[0] == 0x55
    assert resp[7] == 0x01
