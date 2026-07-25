# test_uart_rx.py — 7 unit tests for uart_rx module

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer, ClockCycles, First, FallingEdge

from uart_helpers import (
    CLK_PERIOD_NS, CLKS_PER_BIT, HALF_BIT, BIT_PERIOD_NS,
    uart_send_byte,
)


async def _init(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit='ns').start())
    dut.rst.value = 1
    dut.rx.value  = 1
    await ClockCycles(dut.clk, 5)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 5)


@cocotb.test()
async def test_single_byte_receive(dut):
    """done pulses and data is correct for 0xA5."""
    await _init(dut)
    cocotb.start_soon(uart_send_byte(dut.rx, 0xA5))
    # wait for done to pulse
    for _ in range(CLKS_PER_BIT * 12):
        await RisingEdge(dut.clk)
        if int(dut.done.value) == 1:
            assert int(dut.data.value) == 0xA5, \
                f"Expected 0xA5, got {hex(int(dut.data.value))}"
            return
    assert False, "done never asserted for 0xA5"


@cocotb.test()
async def test_done_pulse_width(dut):
    """done is exactly 1 clock wide."""
    await _init(dut)
    cocotb.start_soon(uart_send_byte(dut.rx, 0x3C))
    for _ in range(CLKS_PER_BIT * 12):
        await RisingEdge(dut.clk)
        if int(dut.done.value) == 1:
            await RisingEdge(dut.clk)
            assert int(dut.done.value) == 0, "done held high for more than 1 clock"
            return
    assert False, "done never asserted"


@cocotb.test()
async def test_all_zeros(dut):
    """0x00 received correctly."""
    await _init(dut)
    cocotb.start_soon(uart_send_byte(dut.rx, 0x00))
    for _ in range(CLKS_PER_BIT * 12):
        await RisingEdge(dut.clk)
        if int(dut.done.value) == 1:
            assert int(dut.data.value) == 0x00
            return
    assert False, "done never asserted for 0x00"


@cocotb.test()
async def test_all_ones(dut):
    """0xFF received correctly."""
    await _init(dut)
    cocotb.start_soon(uart_send_byte(dut.rx, 0xFF))
    for _ in range(CLKS_PER_BIT * 12):
        await RisingEdge(dut.clk)
        if int(dut.done.value) == 1:
            assert int(dut.data.value) == 0xFF
            return
    assert False, "done never asserted for 0xFF"


@cocotb.test()
async def test_consecutive_bytes(dut):
    """5 back-to-back bytes all received in order."""
    await _init(dut)
    test_bytes = [0x12, 0x34, 0x56, 0x78, 0x9A]
    received   = []

    async def collect():
        while len(received) < 5:
            await RisingEdge(dut.clk)
            if int(dut.done.value) == 1:
                received.append(int(dut.data.value))

    cocotb.start_soon(collect())
    for b in test_bytes:
        await uart_send_byte(dut.rx, b)

    # wait for all done pulses
    for _ in range(CLKS_PER_BIT * 12 * 5):
        await RisingEdge(dut.clk)
        if len(received) == 5:
            break

    assert received == test_bytes, f"Expected {test_bytes}, got {received}"


@cocotb.test()
async def test_false_start_rejected(dut):
    """Glitch shorter than HALF_BIT clocks is ignored."""
    await _init(dut)
    # Pulse RX low for only HALF_BIT/2 clocks — too short to be a real start bit
    dut.rx.value = 0
    await ClockCycles(dut.clk, HALF_BIT // 2)
    dut.rx.value = 1
    # Wait 2 full bit times — no done should appear
    for _ in range(CLKS_PER_BIT * 2):
        await RisingEdge(dut.clk)
        assert int(dut.done.value) == 0, "False start triggered a receive"
    # Verify it still receives correctly after the glitch
    cocotb.start_soon(uart_send_byte(dut.rx, 0x55))
    for _ in range(CLKS_PER_BIT * 12):
        await RisingEdge(dut.clk)
        if int(dut.done.value) == 1:
            assert int(dut.data.value) == 0x55
            return
    assert False, "Receiver broken after false start"


@cocotb.test()
async def test_reset_clears_state(dut):
    """Reset mid-receive; receiver works correctly afterward."""
    await _init(dut)
    # Start sending a byte but reset partway through
    dut.rx.value = 0   # start bit
    await Timer(BIT_PERIOD_NS, unit='ns')
    dut.rx.value = 1   # first data bit
    await Timer(BIT_PERIOD_NS // 2, unit='ns')
    # Assert reset
    dut.rst.value = 1
    await ClockCycles(dut.clk, 3)
    dut.rst.value = 0
    dut.rx.value  = 1   # idle
    await ClockCycles(dut.clk, 5)
    # Verify done is not stuck high
    assert int(dut.done.value) == 0, "done stuck high after reset"
    # Now receive a good byte
    cocotb.start_soon(uart_send_byte(dut.rx, 0xBE))
    for _ in range(CLKS_PER_BIT * 12):
        await RisingEdge(dut.clk)
        if int(dut.done.value) == 1:
            assert int(dut.data.value) == 0xBE
            return
    assert False, "Receiver broken after reset"
