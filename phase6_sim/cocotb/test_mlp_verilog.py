# test_mlp_verilog.py — real hls4ml Verilog arithmetic check (3 games)
# DUT: myproject.v (real synthesized Verilog, 37 files) in isolation — no UART.
# Drive feature vectors directly via ap_memory interface.
#
# Pass criteria (real fixed-point arithmetic — allow rounding):
#   win delta  <= 13 counts (±5%)
#   spread delta <= 3 points

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer
import csv
import os
import numpy as np

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

MLP_TEST_GAME_INDICES = [0, 3, 4]
# Game 0: KC vs DET — clear favourite (~73.6%)
# Game 3: CLE vs CIN — close game (~52.4%)
# Game 4: IND vs JAX — slight home favourite (~54.2%)

CLK_PERIOD_NS = 10   # 100 MHz


def _vec(name):
    return os.path.join(_PROJECT_ROOT, 'phase6_sim', 'test_vectors', name)


@cocotb.test()
async def test_mlp_3_game_arithmetic(dut):
    """
    Run 3 games through the real hls4ml Verilog and compare against
    Python quantized model predictions. Verifies that C sim → Verilog
    synthesis did not introduce arithmetic errors.
    """
    cocotb.start_soon(Clock(dut.ap_clk, CLK_PERIOD_NS, unit='ns').start())
    dut.ap_rst.value   = 1
    dut.ap_start.value = 0
    dut.features_q0.value = 0
    await ClockCycles(dut.ap_clk, 5)
    dut.ap_rst.value = 0
    await ClockCycles(dut.ap_clk, 10)

    inputs   = list(csv.DictReader(open(_vec('input_games.csv'))))
    expected = list(csv.DictReader(open(_vec('expected_outputs.csv'))))

    results = []

    for game_idx in MLP_TEST_GAME_INDICES:
        inp = inputs[game_idx]
        exp = expected[game_idx]

        feature_bytes = [int(inp[f'feature_{j}']) for j in range(21)]
        exp_win_u8    = int(exp['win_uint8'])
        exp_spread_i8 = int(exp['spread_int8'])

        # Wait for ap_idle=1
        for _ in range(100):
            await RisingEdge(dut.ap_clk)
            if int(dut.ap_idle.value) == 1:
                break
        else:
            assert False, "ap_idle never asserted"

        # Assert ap_start for exactly 1 clock
        dut.ap_start.value = 1
        await RisingEdge(dut.ap_clk)
        dut.ap_start.value = 0

        fpga_win    = None
        fpga_spread = None

        # Run up to 200000 cycles — respond to feature reads + capture outputs
        # Real MLP latency is 1760 cycles but deep-FIFO sequential stages add overhead
        for _ in range(30000):
            await RisingEdge(dut.ap_clk)

            # Respond to feature memory reads
            if int(dut.features_ce0.value) == 1:
                addr  = int(dut.features_address0.value)
                if addr < 21:
                    # Encoding: ap_fixed<18,6> — byte in fractional bits [11:4]
                    dut.features_q0.value = (feature_bytes[addr] & 0xFF) << 4

            if _ % 5000 == 4999:
                cocotb.log.info(f"Debug cycle {_+1}: ap_idle={int(dut.ap_idle.value)} features_ce0={int(dut.features_ce0.value)} layer9_vld={int(dut.layer9_out_ap_vld.value)}")

            # Capture win output
            if int(dut.layer9_out_ap_vld.value) == 1:
                raw = int(dut.layer9_out.value)
                fpga_win = (raw >> 4) & 0xFF

            # Capture spread output
            if int(dut.layer10_out_ap_vld.value) == 1:
                raw = int(dut.layer10_out.value)
                byte_val = (raw >> 16) & 0xFF
                fpga_spread = byte_val if byte_val < 128 else byte_val - 256

            if fpga_win is not None and fpga_spread is not None:
                break

        assert fpga_win    is not None, f"Game {game_idx}: layer9_out_ap_vld never fired"
        assert fpga_spread is not None, f"Game {game_idx}: layer10_out_ap_vld never fired"

        win_delta    = abs(fpga_win    - exp_win_u8)
        spread_delta = abs(fpga_spread - exp_spread_i8)

        results.append({
            'game_idx':     game_idx,
            'home_team':    inp.get('home_team', '?'),
            'away_team':    inp.get('away_team', '?'),
            'py_win_u8':    exp_win_u8,
            'fpga_win_u8':  fpga_win,
            'win_delta':    win_delta,
            'py_spread':    exp_spread_i8,
            'fpga_spread':  fpga_spread,
            'spread_delta': spread_delta,
        })

        # Wait for ap_idle before next game
        for _ in range(200):
            await RisingEdge(dut.ap_clk)
            if int(dut.ap_idle.value) == 1:
                break

    # Print comparison table
    cocotb.log.info(f"\n{'='*65}")
    cocotb.log.info(f"MLP VERILOG ARITHMETIC CHECK — {len(MLP_TEST_GAME_INDICES)} games")
    cocotb.log.info(f"{'Game':<6} {'Matchup':<14} {'PyWin':>6} {'HWWin':>6} "
                    f"{'WΔ':>4} {'PySprd':>7} {'HWSprd':>7} {'SΔ':>4}")
    cocotb.log.info("-" * 65)
    for r in results:
        cocotb.log.info(
            f"{r['game_idx']:<6} {r['home_team']+'vs'+r['away_team']:<14} "
            f"{r['py_win_u8']:>6d} {r['fpga_win_u8']:>6d} {r['win_delta']:>4d} "
            f"{r['py_spread']:>+7d} {r['fpga_spread']:>+7d} {r['spread_delta']:>4d}"
        )
    cocotb.log.info(f"{'='*65}")

    for r in results:
        assert r['win_delta'] <= 13, \
            f"Game {r['game_idx']}: win delta {r['win_delta']} > 13 counts (±5%)"
        assert r['spread_delta'] <= 3, \
            f"Game {r['game_idx']}: spread delta {r['spread_delta']} > 3 points"
