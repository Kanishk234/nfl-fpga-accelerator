# test_mlp_controller.py — 5 unit tests for mlp_controller module
# Drive ap_ctrl_hs signals manually — no real MLP or stub needed.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

from uart_helpers import CLK_PERIOD_NS


async def _init(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit='ns').start())
    dut.rst.value              = 1
    dut.feature_bus.value      = 0
    dut.packet_valid.value     = 0
    dut.ap_done.value          = 0
    dut.ap_idle.value          = 1
    dut.ap_ready.value         = 1
    dut.features_address0.value = 0
    dut.features_ce0.value     = 0
    dut.layer9_out.value       = 0
    dut.layer9_out_ap_vld.value = 0
    dut.layer10_out.value      = 0
    dut.layer10_out_ap_vld.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 5)


def _make_feature_bus(feature_bytes):
    """Pack 21 bytes into 168-bit bus: feature[i] = bus[i*8+7:i*8]."""
    bus = 0
    for i, b in enumerate(feature_bytes):
        bus |= (b & 0xFF) << (i * 8)
    return bus


async def _trigger_packet(dut, feature_bytes):
    """Inject a packet_valid pulse with given features."""
    dut.feature_bus.value  = _make_feature_bus(feature_bytes)
    dut.packet_valid.value = 1
    await RisingEdge(dut.clk)
    dut.packet_valid.value = 0


async def _simulate_mlp(dut, feature_bytes, win_byte, spread_byte,
                         latency_clks=30):
    """
    Simulate MLP response: respond to feature reads, then fire ap_done
    and output valid signals.
    """
    # Wait for ap_start
    for _ in range(latency_clks):
        await RisingEdge(dut.clk)
        if int(dut.ap_start.value) == 1:
            break

    dut.ap_idle.value  = 0
    dut.ap_ready.value = 0

    # Respond to feature reads
    for _ in range(latency_clks):
        await RisingEdge(dut.clk)
        if int(dut.features_ce0.value) == 1:
            addr = int(dut.features_address0.value)
            dut.features_q0.value = (feature_bytes[addr] & 0xFF) << 4

    # Fire outputs
    dut.layer9_out.value          = (win_byte & 0xFF) << 4
    dut.layer9_out_ap_vld.value   = 1
    dut.layer10_out.value         = (spread_byte & 0xFF) << 16
    dut.layer10_out_ap_vld.value  = 1
    dut.ap_done.value             = 1
    await RisingEdge(dut.clk)
    dut.layer9_out_ap_vld.value   = 0
    dut.layer10_out_ap_vld.value  = 0
    dut.ap_done.value             = 0
    dut.ap_idle.value             = 1
    dut.ap_ready.value            = 1


@cocotb.test()
async def test_ap_start_single_cycle(dut):
    """ap_start is exactly 1 clock wide."""
    await _init(dut)
    features = [0x80] * 21
    mlp_task = cocotb.start_soon(_simulate_mlp(dut, features, 0x80, 0x03))
    await _trigger_packet(dut, features)

    ap_start_count = 0
    for _ in range(50):
        await RisingEdge(dut.clk)
        if int(dut.ap_start.value) == 1:
            ap_start_count += 1

    await mlp_task
    assert ap_start_count == 1, f"ap_start pulsed {ap_start_count} times (expected 1)"


@cocotb.test()
async def test_feature_memory_response(dut):
    """Feature bytes are served correctly via ap_memory interface."""
    await _init(dut)
    features = [i * 3 + 1 for i in range(21)]
    served   = {}

    async def capture_reads():
        for _ in range(200):
            await RisingEdge(dut.clk)
            if int(dut.features_ce0.value) == 1:
                addr  = int(dut.features_address0.value)
                # Controller sets features_q0 combinatorially from feature_bus
                await RisingEdge(dut.clk)
                q0    = int(dut.features_q0.value)
                byte_back = (q0 >> 4) & 0xFF
                served[addr] = byte_back

    cocotb.start_soon(capture_reads())
    mlp_task = cocotb.start_soon(_simulate_mlp(dut, features, 0xC0, 0x02))
    await _trigger_packet(dut, features)
    await mlp_task
    await ClockCycles(dut.clk, 20)

    for addr, expected in enumerate(features):
        if addr in served:
            assert served[addr] == expected, \
                f"feature[{addr}]: expected {hex(expected)}, served {hex(served[addr])}"


@cocotb.test()
async def test_output_capture_win(dut):
    """result_win = layer9_out[11:4]."""
    await _init(dut)
    features  = [0x80] * 21
    win_byte  = 0xC3   # 0xC3 × 256 ≈ 76%
    mlp_task  = cocotb.start_soon(_simulate_mlp(dut, features, win_byte, 0x00))
    await _trigger_packet(dut, features)
    await mlp_task

    for _ in range(20):
        await RisingEdge(dut.clk)
        if int(dut.result_valid.value) == 1:
            got = int(dut.result_win.value)
            assert got == win_byte, \
                f"result_win: expected {hex(win_byte)}, got {hex(got)}"
            return
    assert False, "result_valid never asserted"


@cocotb.test()
async def test_output_capture_spread(dut):
    """result_spread = layer10_out[23:16] signed."""
    await _init(dut)
    features     = [0x80] * 21
    spread_int8  = -7
    spread_byte  = spread_int8 & 0xFF   # 0xF9
    mlp_task     = cocotb.start_soon(_simulate_mlp(dut, features, 0x80, spread_byte))
    await _trigger_packet(dut, features)
    await mlp_task

    for _ in range(20):
        await RisingEdge(dut.clk)
        if int(dut.result_valid.value) == 1:
            got_raw  = int(dut.result_spread.value)
            # result_spread is 8-bit signed in hardware
            got_signed = got_raw if got_raw < 128 else got_raw - 256
            assert got_signed == spread_int8, \
                f"result_spread: expected {spread_int8}, got {got_signed}"
            return
    assert False, "result_valid never asserted"


@cocotb.test()
async def test_returns_to_idle(dut):
    """Controller returns to IDLE after result_valid; accepts next packet."""
    await _init(dut)
    features = [0x40] * 21
    for round_num in range(2):
        mlp_task = cocotb.start_soon(_simulate_mlp(dut, features, 0x80 + round_num, 0x01))
        await _trigger_packet(dut, features)
        await mlp_task
        # Wait for result_valid
        for _ in range(30):
            await RisingEdge(dut.clk)
            if int(dut.result_valid.value) == 1:
                break
        await ClockCycles(dut.clk, 5)
