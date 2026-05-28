# test_integration.py — 4 integration tests for full top.v with myproject_stub.v
# Verifies end-to-end pipeline: UART → framing → controller → stub MLP → response
#
# Critical: always cocotb.start_soon(uart_recv_response()) BEFORE uart_send_packet()
# The 20-cycle stub may respond before the packet send completes.
#
# Instance name in top.v: u_mlp_ip (verified from top.v source)

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge
import functools

from uart_helpers import (
    CLK_PERIOD_NS, SOF_RESPONSE, STATUS_OK, STATUS_NACK,
    init_dut, uart_send_packet, uart_recv_response,
    model_outputs_to_stub_vals,
)


@cocotb.test()
async def test_full_round_trip(dut):
    """Valid packet in → correct 4-byte response out."""
    await init_dut(dut)

    features     = [0x80] * 21   # all-midpoint features
    win_prob     = 0.65
    spread_float = -3.0

    win_18bit, spread_32bit, exp_win_u8, exp_spread_i8 = \
        model_outputs_to_stub_vals(win_prob, spread_float)

    dut.u_mlp_ip.win_val.value    = win_18bit
    dut.u_mlp_ip.spread_val.value = spread_32bit

    recv_task = cocotb.start_soon(uart_recv_response(dut.uart_txd, timeout_clks=300_000))
    await uart_send_packet(dut.uart_rxd, features)
    sof, hw_win, hw_spread_raw, status = await recv_task

    hw_spread = hw_spread_raw if hw_spread_raw < 128 else hw_spread_raw - 256

    assert sof    == SOF_RESPONSE, f"Wrong SOF: {hex(sof)}"
    assert status == STATUS_OK,    f"Status not OK: {hex(status)}"
    assert hw_win == exp_win_u8,   f"Win: expected {exp_win_u8}, got {hw_win}"
    assert hw_spread == exp_spread_i8, f"Spread: expected {exp_spread_i8}, got {hw_spread}"


@cocotb.test()
async def test_checksum_error_nack(dut):
    """Bad checksum → status=0x01 (NACK)."""
    await init_dut(dut)

    features     = [0x40] * 21
    bad_checksum = 0xFF   # deliberately wrong
    packet       = [0xAA] + features + [bad_checksum]

    from uart_helpers import uart_send_byte, uart_recv_response
    recv_task = cocotb.start_soon(uart_recv_response(dut.uart_txd, timeout_clks=300_000))
    for b in packet:
        await uart_send_byte(dut.uart_rxd, b)
    sof, _, _, status = await recv_task

    assert sof    == SOF_RESPONSE, f"Wrong SOF: {hex(sof)}"
    assert status == STATUS_NACK,  f"Expected NACK (0x01), got {hex(status)}"


@cocotb.test()
async def test_back_to_back_inferences(dut):
    """Two packets back to back both complete."""
    await init_dut(dut)

    games = [
        (0.60, -3.0, [0x80] * 21),
        (0.45, +5.0, [0x40] * 21),
    ]

    for win_prob, spread_float, features in games:
        win_18bit, spread_32bit, exp_win_u8, exp_spread_i8 = \
            model_outputs_to_stub_vals(win_prob, spread_float)

        dut.u_mlp_ip.win_val.value    = win_18bit
        dut.u_mlp_ip.spread_val.value = spread_32bit

        recv_task = cocotb.start_soon(uart_recv_response(dut.uart_txd, timeout_clks=300_000))
        await uart_send_packet(dut.uart_rxd, features)
        sof, hw_win, hw_spread_raw, status = await recv_task

        hw_spread = hw_spread_raw if hw_spread_raw < 128 else hw_spread_raw - 256
        assert status  == STATUS_OK,    f"Status not OK: {hex(status)}"
        assert hw_win  == exp_win_u8,   f"Win mismatch: {exp_win_u8} vs {hw_win}"
        assert hw_spread == exp_spread_i8, f"Spread mismatch: {exp_spread_i8} vs {hw_spread}"


@cocotb.test()
async def test_reset_clears_state(dut):
    """Reset mid-packet; next packet succeeds."""
    await init_dut(dut)

    from uart_helpers import uart_send_byte
    # Send SOF + half a packet, then reset
    await uart_send_byte(dut.uart_rxd, 0xAA)
    for _ in range(5):
        await uart_send_byte(dut.uart_rxd, 0x80)

    dut.rst.value = 1
    await ClockCycles(dut.clk, 5)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 5)

    # Full valid packet should now succeed
    features = [0xC0] * 21
    win_18bit, spread_32bit, exp_win_u8, exp_spread_i8 = \
        model_outputs_to_stub_vals(0.70, -7.0)

    dut.u_mlp_ip.win_val.value    = win_18bit
    dut.u_mlp_ip.spread_val.value = spread_32bit

    recv_task = cocotb.start_soon(uart_recv_response(dut.uart_txd, timeout_clks=300_000))
    await uart_send_packet(dut.uart_rxd, features)
    sof, hw_win, hw_spread_raw, status = await recv_task

    assert status == STATUS_OK,  f"Status not OK after reset: {hex(status)}"
    assert sof    == SOF_RESPONSE
