# test_uart_tx.py — 5 unit tests for uart_tx module

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, Timer, ClockCycles, First

from uart_helpers import (
    CLK_PERIOD_NS, CLKS_PER_BIT, BIT_PERIOD_NS,
    uart_recv_byte,
)


async def _init(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit='ns').start())
    dut.rst.value   = 1
    dut.data.value  = 0
    dut.start.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 5)


async def _send_byte(dut, byte_val):
    """Trigger TX and wait for busy to clear."""
    dut.data.value  = byte_val
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0


@cocotb.test()
async def test_tx_idles_high(dut):
    """TX line high when idle."""
    await _init(dut)
    await ClockCycles(dut.clk, 10)
    assert int(dut.tx.value) == 1, f"TX not idle-high: {dut.tx.value}"


@cocotb.test()
async def test_single_byte_transmit(dut):
    """Transmitted byte received correctly."""
    await _init(dut)
    recv_task = cocotb.start_soon(uart_recv_byte(dut.tx, timeout_clks=300_000))
    await _send_byte(dut, 0xA5)
    received = await recv_task
    assert received == 0xA5, f"Expected 0xA5, got {hex(received)}"


@cocotb.test()
async def test_busy_flag(dut):
    """busy asserts during TX, clears at stop bit."""
    await _init(dut)
    await _send_byte(dut, 0x42)
    # busy should be high during transmission
    await RisingEdge(dut.clk)
    assert int(dut.busy.value) == 1, "busy not asserted during TX"
    # wait for busy to clear (10 bit times max)
    for _ in range(CLKS_PER_BIT * 12):
        await RisingEdge(dut.clk)
        if int(dut.busy.value) == 0:
            return
    assert False, "busy never cleared"


@cocotb.test()
async def test_lsb_first(dut):
    """Bit order verified with 0x01 and 0x80."""
    await _init(dut)
    for byte_val in [0x01, 0x80]:
        recv_task = cocotb.start_soon(uart_recv_byte(dut.tx, timeout_clks=300_000))
        await _send_byte(dut, byte_val)
        received = await recv_task
        assert received == byte_val, f"Expected {hex(byte_val)}, got {hex(received)}"
        # wait for busy to clear before next byte
        for _ in range(CLKS_PER_BIT * 2):
            await RisingEdge(dut.clk)
            if int(dut.busy.value) == 0:
                break


@cocotb.test()
async def test_back_to_back_bytes(dut):
    """5 consecutive bytes all correct."""
    await _init(dut)
    test_bytes = [0x11, 0x22, 0x33, 0x44, 0x55]
    received   = []
    for byte_val in test_bytes:
        recv_task = cocotb.start_soon(uart_recv_byte(dut.tx, timeout_clks=300_000))
        await _send_byte(dut, byte_val)
        b = await recv_task
        received.append(b)
        # wait for busy to clear before next
        for _ in range(CLKS_PER_BIT * 2):
            await RisingEdge(dut.clk)
            if int(dut.busy.value) == 0:
                break
    assert received == test_bytes, f"Expected {test_bytes}, got {received}"
