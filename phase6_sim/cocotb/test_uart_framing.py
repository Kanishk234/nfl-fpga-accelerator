"""cocotb unit tests for uart_framing — driven at rx_data/rx_done interface level."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Event
import functools

CLK_PERIOD_NS = 10


async def reset_dut(dut):
    dut.rst.value           = 1
    dut.rx_data.value       = 0
    dut.rx_done.value       = 0
    dut.tx_busy.value       = 0
    dut.result_win.value    = 0
    dut.result_spread.value = 0
    dut.result_valid.value  = 0
    await ClockCycles(dut.clk, 5)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 2)


async def send_byte(dut, byte_val):
    """Deliver one rx byte to the framing module (simulates uart_rx output)."""
    dut.rx_data.value = byte_val
    dut.rx_done.value = 1
    await RisingEdge(dut.clk)
    dut.rx_done.value = 0
    await ClockCycles(dut.clk, 2)


async def send_packet(dut, features, checksum=None):
    """Send a complete 23-byte framing packet."""
    if checksum is None:
        checksum = functools.reduce(lambda a, b: a ^ b, features)
    await send_byte(dut, 0xAA)
    for f in features:
        await send_byte(dut, f)
    await send_byte(dut, checksum)


class PulseMonitor:
    """Monitors packet_valid and packet_error pulses concurrently."""
    def __init__(self, dut):
        self.valid_count = 0
        self.error_count = 0
        self._dut = dut
        self._task = None

    def start(self):
        self._task = cocotb.start_soon(self._run())

    async def _run(self):
        while True:
            await RisingEdge(self._dut.clk)
            if self._dut.packet_valid.value == 1:
                self.valid_count += 1
            if self._dut.packet_error.value == 1:
                self.error_count += 1

    def stop(self):
        if self._task:
            self._task.cancel()


async def capture_tx_bytes(dut, n, timeout=500):
    """Capture n bytes from the TX interface (watch for tx_start pulses)."""
    received = []
    for _ in range(timeout):
        await RisingEdge(dut.clk)
        if dut.tx_start.value == 1:
            received.append(int(dut.tx_data.value))
        if len(received) == n:
            break
    return received


@cocotb.test()
async def test_valid_packet_assembly(dut):
    """Valid 23-byte packet → packet_valid pulses once, feature_bus correct."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)

    features = list(range(21))
    checksum = functools.reduce(lambda a, b: a ^ b, features)

    mon = PulseMonitor(dut)
    mon.start()
    await send_packet(dut, features, checksum)
    await ClockCycles(dut.clk, 5)
    mon.stop()

    assert mon.valid_count == 1, \
        f"packet_valid fired {mon.valid_count} times, expected 1"
    assert mon.error_count == 0, \
        "packet_error fired unexpectedly"

    # Verify feature_bus — feature[i] = feature_bus[i*8 +: 8]
    bus = int(dut.feature_bus.value)
    for i, expected in enumerate(features):
        got = (bus >> (i * 8)) & 0xFF
        assert got == expected, \
            f"feature[{i}]: expected 0x{expected:02X}, got 0x{got:02X}"


@cocotb.test()
async def test_checksum_failure_triggers_error(dut):
    """Wrong checksum → packet_error fires, packet_valid does not."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)

    features     = [0x10] * 21
    bad_checksum = functools.reduce(lambda a, b: a ^ b, features) ^ 0xFF

    mon = PulseMonitor(dut)
    mon.start()
    await send_packet(dut, features, bad_checksum)
    await ClockCycles(dut.clk, 5)
    mon.stop()

    assert mon.error_count == 1, \
        f"packet_error fired {mon.error_count} times, expected 1"
    assert mon.valid_count == 0, \
        "packet_valid fired on a bad checksum"


@cocotb.test()
async def test_sof_resync(dut):
    """5 garbage bytes before a valid packet — only the valid packet fires."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)

    mon = PulseMonitor(dut)
    mon.start()

    # 5 garbage bytes (none are 0xAA)
    for b in [0x01, 0x02, 0x03, 0x04, 0x05]:
        await send_byte(dut, b)

    valid_after_garbage = mon.valid_count

    # Now a valid packet
    features = [0xAB] * 21
    checksum = functools.reduce(lambda a, b: a ^ b, features)
    await send_packet(dut, features, checksum)
    await ClockCycles(dut.clk, 5)
    mon.stop()

    assert valid_after_garbage == 0, \
        "packet_valid fired during garbage bytes"
    assert mon.valid_count == 1, \
        f"packet_valid fired {mon.valid_count} times total, expected 1"


@cocotb.test()
async def test_response_transmission(dut):
    """result_valid with win=0xC0, spread=0xFB → TX bytes [0x55, 0xC0, 0xFB, 0x00]."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)

    dut.result_win.value    = 0xC0
    dut.result_spread.value = 0xFB
    dut.result_valid.value  = 1
    await RisingEdge(dut.clk)
    dut.result_valid.value = 0

    # tx_busy stays 0 — all 4 bytes sent in rapid succession
    tx_bytes = await capture_tx_bytes(dut, 4)

    assert len(tx_bytes) == 4, f"Only got {len(tx_bytes)} TX bytes, expected 4"
    assert tx_bytes[0] == 0x55, f"SOF: expected 0x55, got 0x{tx_bytes[0]:02X}"
    assert tx_bytes[1] == 0xC0, f"WIN: expected 0xC0, got 0x{tx_bytes[1]:02X}"
    assert tx_bytes[2] == 0xFB, f"SPREAD: expected 0xFB, got 0x{tx_bytes[2]:02X}"
    assert tx_bytes[3] == 0x00, f"STATUS: expected 0x00, got 0x{tx_bytes[3]:02X}"


@cocotb.test()
async def test_checksum_computation(dut):
    """XOR of features [0..20] must be accepted by the framing module."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)

    features = list(range(21))
    checksum = functools.reduce(lambda a, b: a ^ b, features)

    mon = PulseMonitor(dut)
    mon.start()
    await send_packet(dut, features, checksum)
    await ClockCycles(dut.clk, 5)
    mon.stop()

    assert mon.valid_count == 1, \
        f"packet_valid fired {mon.valid_count} times, expected 1"
    assert mon.error_count == 0, \
        "packet_error fired — checksum was wrong"
