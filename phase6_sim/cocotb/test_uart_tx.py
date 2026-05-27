"""cocotb unit tests for uart_tx — UART transmitter, 8N1, 115200 baud at 100 MHz."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer, ClockCycles, FallingEdge

CLK_PERIOD_NS = 10
CLKS_PER_BIT  = 868
BIT_PERIOD_NS = CLKS_PER_BIT * CLK_PERIOD_NS


async def reset_dut(dut):
    dut.rst.value   = 1
    dut.data.value  = 0
    dut.start.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst.value   = 0
    await ClockCycles(dut.clk, 2)


async def receive_uart_byte(dut, clk_period_ns=CLK_PERIOD_NS):
    """Sample uart_tx output and decode 8N1 frame."""
    bit_period_ns = CLKS_PER_BIT * clk_period_ns

    # Wait for start bit (falling edge on tx)
    while dut.tx.value != 0:
        await RisingEdge(dut.clk)

    # Sample middle of start bit
    await Timer(bit_period_ns // 2, units='ns')
    assert dut.tx.value == 0, "start bit was not 0"

    received = 0
    for i in range(8):
        await Timer(bit_period_ns, units='ns')
        received |= (int(dut.tx.value) << i)

    # Stop bit
    await Timer(bit_period_ns, units='ns')
    assert dut.tx.value == 1, "stop bit was not 1"

    return received


@cocotb.test()
async def test_tx_idle_line(dut):
    """After reset, tx must be 1 (idle high)."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)
    assert int(dut.tx.value) == 1, f"tx not idle-high after reset, got {dut.tx.value}"


@cocotb.test()
async def test_transmit_byte(dut):
    """Send 0xB7 and verify start bit, 8 LSB-first data bits, stop bit, busy."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)

    dut.data.value  = 0xB7
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0

    # busy should go high
    await RisingEdge(dut.clk)
    assert dut.busy.value == 1, "busy did not go high after start"

    received = await receive_uart_byte(dut)
    assert received == 0xB7, f"Expected 0xB7, got 0x{received:02X}"

    # Poll until busy clears — stop bit finishes ~half-period after our sample point
    for _ in range(1000):
        await RisingEdge(dut.clk)
        if dut.busy.value == 0:
            break
    else:
        assert False, "busy did not return low after transmission"
    assert dut.tx.value == 1, "tx not idle-high after transmission"


@cocotb.test()
async def test_busy_prevents_new_tx(dut):
    """While busy transmitting 0x42, asserting start with 0xFF must be ignored."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)

    # Start transmitting 0x42
    dut.data.value  = 0x42
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    await RisingEdge(dut.clk)  # busy should be high now

    # While busy, try to queue 0xFF
    dut.data.value  = 0xFF
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0

    received = await receive_uart_byte(dut)
    assert received == 0x42, \
        f"Expected 0x42 (busy ignored new byte), got 0x{received:02X}"

    # Poll until busy clears then verify no second transmission starts
    for _ in range(1000):
        await RisingEdge(dut.clk)
        if dut.busy.value == 0:
            break
    else:
        assert False, "busy never cleared after first byte"

    # Wait more — confirm no second byte transmission starts
    await ClockCycles(dut.clk, 50)
    assert dut.busy.value == 0, "unexpected transmission after busy block"
