# uart_helpers.py
# Shared constants and coroutines used by all Phase 6 test files.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, Timer, First, ClockCycles

CLK_PERIOD_NS = 10          # 100 MHz
CLKS_PER_BIT  = 868         # 100_000_000 / 115_200
HALF_BIT      = 434         # mid-bit sampling offset
BIT_PERIOD_NS = CLKS_PER_BIT * CLK_PERIOD_NS   # 8680 ns

SOF_REQUEST   = 0xAA
SOF_RESPONSE  = 0x55
STATUS_OK     = 0x00
STATUS_NACK   = 0x01


async def init_dut(dut, rx_port_name='uart_rxd'):
    """Start clock, assert reset for 5 cycles, deassert."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit='ns').start())
    dut.rst.value = 1
    getattr(dut, rx_port_name).value = 1   # idle high
    await ClockCycles(dut.clk, 5)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 5)


async def uart_send_byte(signal, byte_val):
    """Drive one UART byte (8N1) onto signal at 115200 baud."""
    signal.value = 0                            # start bit
    await Timer(BIT_PERIOD_NS, unit='ns')
    for i in range(8):                          # 8 data bits LSB first
        signal.value = (byte_val >> i) & 1
        await Timer(BIT_PERIOD_NS, unit='ns')
    signal.value = 1                            # stop bit
    await Timer(BIT_PERIOD_NS, unit='ns')


async def uart_send_packet(dut_rx, feature_bytes):
    """Build and send a complete 23-byte request packet."""
    import functools
    checksum = functools.reduce(lambda a, b: a ^ b, feature_bytes)
    packet   = [SOF_REQUEST] + list(feature_bytes) + [checksum]
    for byte in packet:
        await uart_send_byte(dut_rx, byte)


async def uart_recv_byte(dut_tx, timeout_clks=300_000):
    """
    Wait for falling edge on dut_tx (start bit), sample 8 data bits.
    Always use FallingEdge — never poll. Caller must start this BEFORE
    sending the packet to avoid missing fast responses.
    """
    timeout_ns = timeout_clks * CLK_PERIOD_NS
    result = await First(FallingEdge(dut_tx), Timer(timeout_ns, unit='ns'))
    assert int(dut_tx.value) == 0, \
        f"Timeout waiting for UART byte after {timeout_clks} clocks"
    # sample middle of start bit
    await Timer(BIT_PERIOD_NS // 2, unit='ns')
    assert int(dut_tx.value) == 0, "Invalid start bit"
    received = 0
    for i in range(8):
        await Timer(BIT_PERIOD_NS, unit='ns')
        received |= (int(dut_tx.value) << i)
    await Timer(BIT_PERIOD_NS, unit='ns')   # stop bit
    return received


async def uart_recv_response(dut_tx, timeout_clks=300_000):
    """Receive a 4-byte response packet. Returns (sof, win, spread, status)."""
    bytes_rcvd = []
    for _ in range(4):
        b = await uart_recv_byte(dut_tx, timeout_clks)
        bytes_rcvd.append(b)
    return tuple(bytes_rcvd)


def encode_features(scaler, feature_names, game_row):
    """Scale a game row and encode as 21 uint8 bytes."""
    import numpy as np
    X    = scaler.transform(game_row[feature_names].values.reshape(1,-1).astype('float32'))
    return np.clip(np.round(X[0] * 255), 0, 255).astype(int).tolist()


def model_outputs_to_stub_vals(win_prob_float, spread_float):
    """
    Convert Python model predictions to the 18-bit / 32-bit values the stub
    should drive. Mirrors the encoding in mlp_controller.v exactly.

    win:    layer9_out[11:4] = win_uint8 → reconstruct: win_uint8 << 4
    spread: layer10_out[23:16] = spread_int8 → reconstruct: spread_byte << 16

    Port widths verified against phase4_hls synthesized Verilog:
      layer9_out [17:0], layer10_out [31:0]
    """
    import numpy as np
    win_uint8   = int(np.clip(round(win_prob_float * 256), 0, 255))
    spread_int8 = int(np.clip(round(spread_float), -128, 127))
    spread_byte = spread_int8 & 0xFF             # two's complement
    win_18bit   = (win_uint8 & 0xFF) << 4        # byte in fractional bits [11:4]
    spread_32bit = (spread_byte & 0xFF) << 16    # byte in integer bits [23:16]
    return win_18bit, spread_32bit, win_uint8, spread_int8
