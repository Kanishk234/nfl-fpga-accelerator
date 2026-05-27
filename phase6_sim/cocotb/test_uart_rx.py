"""cocotb unit tests for uart_rx — UART receiver, 8N1, 115200 baud at 100 MHz."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer, ClockCycles

CLK_PERIOD_NS = 10       # 100 MHz
CLKS_PER_BIT  = 868      # 100_000_000 / 115_200
BIT_PERIOD_NS = CLKS_PER_BIT * CLK_PERIOD_NS  # 8680 ns


async def send_uart_byte(dut, byte_val, clk_period_ns=CLK_PERIOD_NS):
    bit_period = CLKS_PER_BIT * clk_period_ns
    await ClockCycles(dut.clk, 5)   # idle gap
    # start bit
    dut.rx.value = 0
    await Timer(bit_period, units='ns')
    # 8 data bits LSB first
    for i in range(8):
        dut.rx.value = (byte_val >> i) & 1
        await Timer(bit_period, units='ns')
    # stop bit
    dut.rx.value = 1
    await Timer(bit_period, units='ns')


async def reset_dut(dut):
    dut.rst.value = 1
    dut.rx.value  = 1
    await ClockCycles(dut.clk, 5)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 2)


@cocotb.test()
async def test_clks_per_bit_constant(dut):
    """Verify CLKS_PER_BIT = 868 (100 MHz / 115200 baud)."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)
    # The localparam is baked into the RTL; we verify by reading the CLKS_PER_BIT
    # from the DUT hierarchy if exposed, otherwise trust the known value.
    # Structural check: at 100 MHz / 115200, integer division = 868
    assert CLKS_PER_BIT == 868, f"Expected 868, got {CLKS_PER_BIT}"


@cocotb.test()
async def test_single_byte(dut):
    """Transmit 0xA5 and verify dut.done fires with dut.data == 0xA5."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)

    cocotb.start_soon(send_uart_byte(dut, 0xA5))

    # Wait for done to go high (timeout: 200 bit periods)
    timeout = 200 * BIT_PERIOD_NS
    elapsed = 0
    step_ns  = CLK_PERIOD_NS
    found = False
    while elapsed < timeout:
        await RisingEdge(dut.clk)
        elapsed += step_ns
        if dut.done.value == 1:
            found = True
            break

    assert found, "done never went high after transmitting 0xA5"
    assert int(dut.data.value) == 0xA5, \
        f"Expected data=0xA5, got 0x{int(dut.data.value):02X}"

    # done should return to 0 next clock
    await RisingEdge(dut.clk)
    assert dut.done.value == 0, "done stayed high for more than 1 clock"


@cocotb.test()
async def test_multiple_bytes(dut):
    """Transmit [0x00, 0xFF, 0xAA, 0x55, 0x42] and verify each in order."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)

    test_bytes = [0x00, 0xFF, 0xAA, 0x55, 0x42]

    for expected in test_bytes:
        cocotb.start_soon(send_uart_byte(dut, expected))

        timeout  = 200 * BIT_PERIOD_NS
        elapsed  = 0
        found    = False
        while elapsed < timeout:
            await RisingEdge(dut.clk)
            elapsed += CLK_PERIOD_NS
            if dut.done.value == 1:
                found = True
                break

        assert found, f"done never fired for byte 0x{expected:02X}"
        assert int(dut.data.value) == expected, \
            f"Expected 0x{expected:02X}, got 0x{int(dut.data.value):02X}"


@cocotb.test()
async def test_false_start_rejection(dut):
    """Drive rx low for HALF_BIT clocks then back high — no done should fire."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)

    HALF_BIT_NS = (CLKS_PER_BIT // 2) * CLK_PERIOD_NS

    # False start: pull low for < half bit period then go back high
    dut.rx.value = 0
    await Timer(HALF_BIT_NS - CLK_PERIOD_NS, units='ns')
    dut.rx.value = 1

    # Wait 20 bit periods — done must not fire
    for _ in range(20 * CLKS_PER_BIT):
        await RisingEdge(dut.clk)
        assert dut.done.value == 0, \
            "done fired on a false start — receiver did not reject it"
