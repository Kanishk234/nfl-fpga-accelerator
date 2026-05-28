# test_uart_framing.py — 5 unit tests for uart_framing module
# Uses send_byte_direct() which pulses rx_done directly — bypasses uart_rx
# timing to test the framing FSM in isolation.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

from uart_helpers import CLK_PERIOD_NS, SOF_REQUEST
import functools


async def _init(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit='ns').start())
    dut.rst.value           = 1
    dut.rx_data.value       = 0
    dut.rx_done.value       = 0
    dut.tx_busy.value       = 0
    dut.result_win.value    = 0
    dut.result_spread.value = 0
    dut.result_valid.value  = 0
    await ClockCycles(dut.clk, 5)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 5)


async def send_byte_direct(dut, byte_val):
    """Inject a byte directly by pulsing rx_done for 1 clock."""
    dut.rx_data.value = byte_val
    dut.rx_done.value = 1
    await RisingEdge(dut.clk)
    dut.rx_done.value = 0
    await ClockCycles(dut.clk, 2)   # let FSM process


async def send_packet_direct(dut, feature_bytes):
    """Send a complete valid 23-byte packet via rx_done injection."""
    checksum = functools.reduce(lambda a, b: a ^ b, feature_bytes)
    packet   = [SOF_REQUEST] + list(feature_bytes) + [checksum]
    for b in packet:
        await send_byte_direct(dut, b)


async def _wait_for_signal(dut_signal, max_clks, clk):
    """Poll for signal to go high; return True if found within max_clks."""
    for _ in range(max_clks):
        await RisingEdge(clk)
        if int(dut_signal.value) == 1:
            return True
    return False


@cocotb.test()
async def test_sof_detection(dut):
    """Non-0xAA bytes do not trigger packet_valid."""
    await _init(dut)
    for b in [0x00, 0xFF, 0x55, 0xBB]:
        await send_byte_direct(dut, b)
        assert int(dut.packet_valid.value) == 0, \
            f"packet_valid triggered by non-SOF byte {hex(b)}"


@cocotb.test()
async def test_valid_packet_accepted(dut):
    """packet_valid pulses after correct 23-byte packet."""
    await _init(dut)
    features = list(range(21))

    # Monitor for packet_valid concurrently — it's a 1-cycle pulse that fires
    # during the last byte of send_packet_direct, so we must watch before sending.
    found = []

    async def monitor():
        for _ in range(300):
            await RisingEdge(dut.clk)
            if int(dut.packet_valid.value) == 1:
                found.append(True)
                return

    cocotb.start_soon(monitor())
    await send_packet_direct(dut, features)
    await ClockCycles(dut.clk, 10)

    assert found, "packet_valid never asserted after valid packet"


@cocotb.test()
async def test_checksum_error_triggers_nack(dut):
    """Bad checksum → packet_error asserts."""
    await _init(dut)
    features     = [0xAB] * 21
    bad_checksum = 0x00   # deliberately wrong
    packet       = [SOF_REQUEST] + features + [bad_checksum]

    found_error = []

    async def monitor():
        for _ in range(300):
            await RisingEdge(dut.clk)
            if int(dut.packet_error.value) == 1:
                found_error.append(True)
                return

    cocotb.start_soon(monitor())
    for b in packet:
        await send_byte_direct(dut, b)
    await ClockCycles(dut.clk, 10)

    assert found_error, "packet_error never asserted on bad checksum"


@cocotb.test()
async def test_feature_bus_contents(dut):
    """All 21 feature bytes land on correct feature_bus bits."""
    await _init(dut)
    features = [i * 5 + 10 for i in range(21)]   # distinct values

    found = []

    async def monitor():
        for _ in range(300):
            await RisingEdge(dut.clk)
            if int(dut.packet_valid.value) == 1:
                found.append(int(dut.feature_bus.value))
                return

    cocotb.start_soon(monitor())
    await send_packet_direct(dut, features)
    await ClockCycles(dut.clk, 10)

    assert found, "packet_valid never asserted"
    bus = found[0]
    for i, expected in enumerate(features):
        actual = (bus >> (i * 8)) & 0xFF
        assert actual == expected, \
            f"feature[{i}]: expected {hex(expected)}, got {hex(actual)}"


@cocotb.test()
async def test_resync_after_bad_packet(dut):
    """Valid packet accepted after prior bad one."""
    await _init(dut)
    # Send bad packet
    features_bad = [0xAB] * 21
    packet_bad   = [SOF_REQUEST] + features_bad + [0x00]   # wrong checksum
    for b in packet_bad:
        await send_byte_direct(dut, b)
    await ClockCycles(dut.clk, 10)

    # Now send a good packet — monitor concurrently
    features_good = list(range(21))
    found = []

    async def monitor():
        for _ in range(300):
            await RisingEdge(dut.clk)
            if int(dut.packet_valid.value) == 1:
                found.append(True)
                return

    cocotb.start_soon(monitor())
    await send_packet_direct(dut, features_good)
    await ClockCycles(dut.clk, 10)

    assert found, "packet_valid never asserted after resync"
