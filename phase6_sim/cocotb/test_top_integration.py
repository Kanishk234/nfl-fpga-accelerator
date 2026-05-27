"""cocotb integration tests for top.v using myproject_stub_fast.v.
Full pipeline: uart_rxd → framing → controller → stub MLP → framing → uart_txd.
"""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, Timer, ClockCycles, First
import functools

CLK_PERIOD_NS = 10
CLKS_PER_BIT  = 868
BIT_PERIOD_NS = CLKS_PER_BIT * CLK_PERIOD_NS   # 8680 ns

RESP_TIMEOUT_CLKS = 250_000  # covers full packet RX (23 bytes) + MLP + TX; ~200k clocks needed


async def reset_dut(dut):
    dut.rst.value      = 1
    dut.uart_rxd.value = 1   # idle high
    await ClockCycles(dut.clk, 10)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 5)


async def send_uart_byte(dut, byte_val):
    """Drive one 8N1 UART byte onto uart_rxd."""
    # start bit
    dut.uart_rxd.value = 0
    await Timer(BIT_PERIOD_NS, units='ns')
    # 8 data bits LSB first
    for i in range(8):
        dut.uart_rxd.value = (byte_val >> i) & 1
        await Timer(BIT_PERIOD_NS, units='ns')
    # stop bit
    dut.uart_rxd.value = 1
    await Timer(BIT_PERIOD_NS, units='ns')


async def send_packet(dut, features, checksum=None):
    """Send a complete 23-byte request packet."""
    if checksum is None:
        checksum = functools.reduce(lambda a, b: a ^ b, features)
    await send_uart_byte(dut, 0xAA)
    for f in features:
        await send_uart_byte(dut, f)
    await send_uart_byte(dut, checksum)


async def receive_uart_byte(dut):
    """Sample uart_txd and decode one 8N1 frame."""
    TIMEOUT_NS = RESP_TIMEOUT_CLKS * CLK_PERIOD_NS
    await First(FallingEdge(dut.uart_txd), Timer(TIMEOUT_NS, units='ns'))
    assert int(dut.uart_txd.value) == 0, "Timeout waiting for uart_txd start bit"

    # Sample middle of start bit
    await Timer(BIT_PERIOD_NS // 2, units='ns')

    received = 0
    for i in range(8):
        await Timer(BIT_PERIOD_NS, units='ns')
        received |= (int(dut.uart_txd.value) << i)

    # Stop bit
    await Timer(BIT_PERIOD_NS, units='ns')
    return received


async def receive_response(dut):
    """Receive the 4-byte response packet from uart_txd."""
    return [await receive_uart_byte(dut) for _ in range(4)]


@cocotb.test()
async def test_full_round_trip_known_values(dut):
    """23-byte valid packet → stub MLP → 4-byte response [0x55, 0xC0, 0xFB, 0x00]."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)

    features = [128] * 21
    checksum = functools.reduce(lambda a, b: a ^ b, features)   # = 128 = 0x80

    # Start receiver before sending: DUT responds while packet is still in-flight
    # (uart_rx processes bytes real-time; MLP takes only 20 clocks).
    receiver = cocotb.start_soon(receive_response(dut))
    await send_packet(dut, features, checksum)
    response = await receiver

    assert response[0] == 0x55, f"SOF: expected 0x55, got 0x{response[0]:02X}"
    assert response[1] == 0xC0, f"WIN: expected 0xC0, got 0x{response[1]:02X}"
    assert response[2] == 0xFB, f"SPREAD: expected 0xFB, got 0x{response[2]:02X}"
    assert response[3] == 0x00, f"STATUS: expected 0x00, got 0x{response[3]:02X}"


@cocotb.test()
async def test_checksum_error_response(dut):
    """Wrong checksum → response with status=0x01 (NACK), ap_start must NOT fire."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)

    features     = [0x42] * 21
    bad_checksum = functools.reduce(lambda a, b: a ^ b, features) ^ 0xFF

    receiver = cocotb.start_soon(receive_response(dut))
    await send_packet(dut, features, bad_checksum)
    response = await receiver

    assert response[0] == 0x55, f"SOF: expected 0x55, got 0x{response[0]:02X}"
    assert response[3] == 0x01, f"STATUS: expected 0x01 (NACK), got 0x{response[3]:02X}"


@cocotb.test()
async def test_back_to_back_inferences(dut):
    """Two consecutive valid packets → two correct responses in order."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)

    features = [64] * 21
    checksum = functools.reduce(lambda a, b: a ^ b, features)   # = 64

    # First packet
    receiver1 = cocotb.start_soon(receive_response(dut))
    await send_packet(dut, features, checksum)
    response1 = await receiver1

    # Second packet
    receiver2 = cocotb.start_soon(receive_response(dut))
    await send_packet(dut, features, checksum)
    response2 = await receiver2

    assert response1[0] == 0x55 and response1[3] == 0x00, \
        f"First response invalid: {[hex(b) for b in response1]}"
    assert response2[0] == 0x55 and response2[3] == 0x00, \
        f"Second response invalid: {[hex(b) for b in response2]}"
    assert response1[1] == response2[1], "Back-to-back win bytes differ (unexpected)"
    assert response1[2] == response2[2], "Back-to-back spread bytes differ (unexpected)"


@cocotb.test()
async def test_reset_clears_state(dut):
    """Reset mid-transmission clears state; subsequent valid packet succeeds."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)

    features = [0x77] * 21
    checksum = functools.reduce(lambda a, b: a ^ b, features)

    # Send SOF + 10 feature bytes (partial packet), then reset
    await send_uart_byte(dut, 0xAA)
    for f in features[:10]:
        await send_uart_byte(dut, f)

    dut.rst.value = 1
    await ClockCycles(dut.clk, 5)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 5)

    # Complete valid packet — start receiver first (same timing issue)
    receiver = cocotb.start_soon(receive_response(dut))
    await send_packet(dut, features, checksum)
    response = await receiver

    assert response[0] == 0x55, f"SOF: expected 0x55, got 0x{response[0]:02X}"
    assert response[3] == 0x00, f"STATUS: expected 0x00, got 0x{response[3]:02X}"
    assert response[1] == 0xC0, f"WIN: expected 0xC0, got 0x{response[1]:02X}"
    assert response[2] == 0xFB, f"SPREAD: expected 0xFB, got 0x{response[2]:02X}"
