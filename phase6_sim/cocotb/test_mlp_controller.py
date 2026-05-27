"""cocotb unit tests for mlp_controller — ap_ctrl_hs FSM and feature memory interface."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

CLK_PERIOD_NS = 10


async def reset_dut(dut):
    dut.rst.value                = 1
    dut.feature_bus.value        = 0
    dut.packet_valid.value       = 0
    dut.ap_done.value            = 0
    dut.ap_idle.value            = 1   # MLP starts idle
    dut.ap_ready.value           = 1
    dut.features_address0.value  = 0
    dut.features_ce0.value       = 0
    dut.layer9_out.value         = 0
    dut.layer9_out_ap_vld.value  = 0
    dut.layer10_out.value        = 0
    dut.layer10_out_ap_vld.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 2)


def build_feature_bus(features):
    """Pack list of 21 uint8 values into a 168-bit integer."""
    bus = 0
    for i, f in enumerate(features):
        bus |= (f & 0xFF) << (i * 8)
    return bus


async def start_inference(dut, features=None):
    """Assert packet_valid for 1 clock and wait for ap_start."""
    if features is not None:
        dut.feature_bus.value = build_feature_bus(features)
    dut.packet_valid.value = 1
    await RisingEdge(dut.clk)
    dut.packet_valid.value = 0


async def simulate_mlp_done(dut, win_out=0x00C00, spread_out=0xFFFB0000, delay=5):
    """After delay clocks, drive both ap_vld signals to simulate MLP completing."""
    await ClockCycles(dut.clk, delay)
    dut.layer9_out.value         = win_out
    dut.layer9_out_ap_vld.value  = 1
    dut.layer10_out.value        = spread_out
    dut.layer10_out_ap_vld.value = 1
    await RisingEdge(dut.clk)
    dut.layer9_out_ap_vld.value  = 0
    dut.layer10_out_ap_vld.value = 0
    dut.ap_done.value            = 1
    await RisingEdge(dut.clk)
    dut.ap_done.value = 0


@cocotb.test()
async def test_idle_waits_for_packet(dut):
    """At reset: ap_start=0, result_valid=0, controller stays idle without packet_valid."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)

    for _ in range(20):
        await RisingEdge(dut.clk)
        assert dut.ap_start.value == 0, "ap_start fired without packet_valid"
        assert dut.result_valid.value == 0, "result_valid fired without packet_valid"


@cocotb.test()
async def test_ap_start_one_cycle_wide(dut):
    """ap_start must be high for exactly 1 clock after packet_valid."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)

    features = [128] * 21
    dut.feature_bus.value = build_feature_bus(features)
    dut.packet_valid.value = 1
    await RisingEdge(dut.clk)
    dut.packet_valid.value = 0

    high_count = 0
    for _ in range(10):
        await RisingEdge(dut.clk)
        if dut.ap_start.value == 1:
            high_count += 1

    assert high_count == 1, \
        f"ap_start was high for {high_count} clocks, expected exactly 1"


@cocotb.test()
async def test_feature_read_response(dut):
    """MLP drives features_ce0 + features_address0; controller responds with correct features_q0."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)

    features = list(range(21))  # 0,1,...,20
    await start_inference(dut, features)

    # Wait for ap_start to fire (controller enters WAIT_DONE)
    for _ in range(10):
        await RisingEdge(dut.clk)
        if dut.ap_start.value == 0:
            break

    # Drive feature reads and check response on next clock
    for addr in [0, 5, 10, 20]:
        dut.features_address0.value = addr
        dut.features_ce0.value = 1
        await RisingEdge(dut.clk)   # DUT samples CE here
        dut.features_ce0.value = 0
        await RisingEdge(dut.clk)   # features_q0 updated (synchronous read)

        expected_q0 = (features[addr] << 4)   # {6'b0, byte, 4'b0}
        got_q0 = int(dut.features_q0.value)
        assert got_q0 == expected_q0, \
            f"feature[{addr}]: expected q0=0x{expected_q0:05X}, got 0x{got_q0:05X}"


@cocotb.test()
async def test_output_capture_and_result_valid(dut):
    """ap_vld signals → result_valid fires for 1 clock with correct win/spread bytes."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)

    await start_inference(dut)
    # Wait for ap_start then clear it
    for _ in range(5):
        await RisingEdge(dut.clk)

    # 0x00C00 = ap_fixed<18,6> value 0.75 → bits[11:4] = 0xC0
    dut.layer9_out.value         = 0x00C00
    dut.layer9_out_ap_vld.value  = 1
    dut.layer10_out.value        = 0xFFFB0000  # bit[23:16] = 0xFB = -5
    dut.layer10_out_ap_vld.value = 1
    dut.ap_done.value            = 1
    await RisingEdge(dut.clk)
    dut.layer9_out_ap_vld.value  = 0
    dut.layer10_out_ap_vld.value = 0
    dut.ap_done.value            = 0

    # Wait for result_valid
    valid_count = 0
    for _ in range(10):
        await RisingEdge(dut.clk)
        if dut.result_valid.value == 1:
            valid_count += 1
            assert int(dut.result_win.value) == 0xC0, \
                f"result_win: expected 0xC0, got 0x{int(dut.result_win.value):02X}"
            assert int(dut.result_spread.value) == 0xFB, \
                f"result_spread: expected 0xFB, got 0x{int(dut.result_spread.value):02X}"

    assert valid_count == 1, f"result_valid fired {valid_count} times, expected 1"


@cocotb.test()
async def test_ap_vld_before_ap_done(dut):
    """ap_vld can arrive before ap_done; controller must exit WAIT_DONE on vld, not ap_done."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)

    await start_inference(dut)
    # Wait past ap_start
    for _ in range(5):
        await RisingEdge(dut.clk)

    # Drive ap_vld 2 cycles BEFORE ap_done
    dut.layer9_out.value         = 0x00C00
    dut.layer9_out_ap_vld.value  = 1
    dut.layer10_out.value        = 0xFFFB0000
    dut.layer10_out_ap_vld.value = 1
    await RisingEdge(dut.clk)
    dut.layer9_out_ap_vld.value  = 0
    dut.layer10_out_ap_vld.value = 0

    # 2 cycles before ap_done: result_valid should fire here before ap_done
    result_valid_before_ap_done = False
    for _ in range(5):
        await RisingEdge(dut.clk)
        if dut.result_valid.value == 1:
            result_valid_before_ap_done = True
            break

    # Now drive ap_done — should have no effect (already done)
    dut.ap_done.value = 1
    await RisingEdge(dut.clk)
    dut.ap_done.value = 0

    assert result_valid_before_ap_done, \
        "result_valid did not fire before ap_done — controller incorrectly waits for ap_done"


@cocotb.test()
async def test_controller_returns_to_idle(dut):
    """After one inference, controller resets to IDLE and accepts a second packet."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, units='ns').start())
    await reset_dut(dut)

    # First inference
    await start_inference(dut, [0x10] * 21)
    for _ in range(5):
        await RisingEdge(dut.clk)

    cocotb.start_soon(simulate_mlp_done(dut))

    # Wait for result_valid
    found = False
    for _ in range(20):
        await RisingEdge(dut.clk)
        if dut.result_valid.value == 1:
            found = True
            break
    assert found, "First inference result_valid never fired"

    # After result_valid, controller should return to IDLE
    await ClockCycles(dut.clk, 3)

    # Second inference — ap_start must fire again
    await start_inference(dut, [0x20] * 21)

    ap_start_fired = False
    for _ in range(10):
        await RisingEdge(dut.clk)
        if dut.ap_start.value == 1:
            ap_start_fired = True
            break

    assert ap_start_fired, "Second inference: ap_start never fired (controller stuck)"
