"""Phase 5 test suite — HDL structure, syntax, and script checks.

Tests marked synthesis=True are skipped by default (they require Vivado on Windows).
Run non-synthesis tests only:
    pytest tests/test_phase5.py -v -k "not synthesis"
"""
import json
import subprocess
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HDL_DIR    = REPO / "mlp/phase5_fpga" / "hdl"
CONST_DIR  = REPO / "mlp/phase5_fpga" / "constraints"
SCRIPT_DIR = REPO / "mlp/phase5_fpga" / "scripts"
ARTIFACT   = REPO / "artifacts"


# ---------------------------------------------------------------------------
# File existence checks
# ---------------------------------------------------------------------------

class TestFilesExist:
    def test_uart_rx(self):
        assert (HDL_DIR / "uart_rx.v").exists()

    def test_uart_tx(self):
        assert (HDL_DIR / "uart_tx.v").exists()

    def test_uart_framing(self):
        assert (HDL_DIR / "uart_framing.v").exists()

    def test_mlp_controller(self):
        assert (HDL_DIR / "mlp_controller.v").exists()

    def test_top(self):
        assert (HDL_DIR / "top.v").exists()

    def test_constraints_xdc(self):
        assert (CONST_DIR / "basys3.xdc").exists()

    def test_create_project_tcl(self):
        assert (SCRIPT_DIR / "create_project.tcl").exists()

    def test_run_synth_tcl(self):
        assert (SCRIPT_DIR / "run_synth.tcl").exists()

    def test_parse_reports_py(self):
        assert (SCRIPT_DIR / "parse_reports.py").exists()

    def test_ip_zip_in_artifacts(self):
        assert (ARTIFACT / "xilinx_com_hls_myproject_1_0.zip").exists(), \
            "Phase 4 IP zip must be in artifacts/"


# ---------------------------------------------------------------------------
# HDL content checks
# ---------------------------------------------------------------------------

class TestHDLContent:
    def _read(self, filename):
        return (HDL_DIR / filename).read_text()

    def test_uart_rx_clks_per_bit(self):
        text = self._read("uart_rx.v")
        assert "CLK_FREQ / BAUD_RATE" in text or "868" in text, \
            "uart_rx.v must define CLKS_PER_BIT = 868"

    def test_uart_rx_double_register(self):
        text = self._read("uart_rx.v")
        assert "rx_meta" in text and "rx_sync" in text, \
            "uart_rx.v must double-register the rx input for metastability protection"

    def test_uart_tx_idle_high(self):
        text = self._read("uart_tx.v")
        # tx must be set to 1 in reset or IDLE
        assert "tx       <= 1'b1" in text or "tx <= 1'b1" in text, \
            "uart_tx.v must idle tx high"

    def test_uart_framing_sof_aa(self):
        text = self._read("uart_framing.v")
        assert "8'hAA" in text or "8'haa" in text.lower(), \
            "uart_framing.v must use 0xAA as SOF marker"

    def test_uart_framing_sof_55(self):
        text = self._read("uart_framing.v")
        assert "8'h55" in text, \
            "uart_framing.v must use 0x55 as TX SOF marker"

    def test_uart_framing_21_features(self):
        text = self._read("uart_framing.v")
        # byte_cnt goes up to 20 (0..20 = 21 features). The old `or "20" in text`
        # fallback made this tautological — "20" appears in any nontrivial file.
        assert "byte_cnt == 5'd20" in text, \
            "uart_framing.v must collect exactly 21 features (byte_cnt 0-20)"

    def test_uart_framing_feature_bus_width(self):
        text = self._read("uart_framing.v")
        assert "167:0" in text, \
            "uart_framing.v must have 168-bit feature_bus (21 × 8 bits)"

    def test_uart_framing_xor_checksum(self):
        text = self._read("uart_framing.v")
        assert "checksum ^ rx_data" in text or "^ rx_data" in text, \
            "uart_framing.v must compute XOR checksum"

    # NOTE (2026-07-26): the four tests below previously asserted the ap_memory
    # interface (`assign ap_start`, `features_q0`, an [11:4] win slice). That
    # interface was deleted by the io_stream rewrite that fixed the in-hardware
    # deadlock (AUDIT_REPORT.md §1), so two of them failed and two passed only by
    # matching substrings in comments. Rewritten against the shipped AXI-Stream
    # design — the one that is actually in the bitstream.

    def test_mlp_controller_ap_start_registered_and_held(self):
        text = self._read("mlp_controller.v")
        assert "output reg         ap_start" in text, \
            "ap_start must be a registered output (ap_ctrl_hs), not a combinational assign"
        assert "ap_start          <= 1'b1;" in text, \
            "mlp_controller.v must raise ap_start when it launches an inference"

    def test_mlp_controller_packs_one_672_bit_beat(self):
        text = self._read("mlp_controller.v")
        assert "output reg [671:0] features_TDATA" in text, \
            "features must be a 672-bit AXI-Stream word (21 lanes x 32 bits)"
        # lane encoding: byte lands at bits [11:4] of its 32-bit lane
        assert "features_TDATA[i*32 +: 32] <= {20'b0, feature_bus[i*8 +: 8], 4'b0}" in text, \
            "each feature byte must be packed as {20'b0, byte, 4'b0} into its lane"

    def test_mlp_controller_holds_tvalid_until_tready(self):
        text = self._read("mlp_controller.v")
        for sig in ["features_TVALID", "features_TREADY",
                    "layer9_out_TREADY", "layer10_out_TREADY"]:
            assert sig in text, f"mlp_controller.v is missing AXI-Stream signal {sig}"

    def test_mlp_controller_spread_bit_extract(self):
        text = self._read("mlp_controller.v")
        # spread arrives as ap_fixed<32,16>; the integer byte is bits [23:16]
        assert "result_spread <= spread_word[23:16]" in text, \
            "mlp_controller.v must take the spread integer byte from bits [23:16]"

    def test_top_instantiates_myproject(self):
        text = self._read("top.v")
        assert "myproject" in text, \
            "top.v must instantiate the myproject hls4ml IP"

    def test_top_port_names(self):
        """top.v must wire the io_stream IP's AXI-Stream ports.

        Was asserting the ap_memory ports (features_address0/ce0/q0,
        layer9_out_ap_vld) that the io_stream rewrite removed — see the note above.
        """
        text = self._read("top.v")
        for port in ["features_TDATA", "features_TVALID", "features_TREADY",
                     "layer9_out_TDATA", "layer9_out_TVALID", "layer9_out_TREADY",
                     "layer10_out_TDATA", "layer10_out_TVALID", "layer10_out_TREADY"]:
            assert port in text, f"top.v is missing port {port}"

    def test_top_has_no_ap_memory_ports(self):
        """Guard against a regression to the interface that deadlocked on silicon."""
        text = self._read("top.v")
        for dead in ["features_address0", "features_ce0", "features_q0",
                     "layer9_out_ap_vld", "layer10_out_ap_vld"]:
            assert dead not in text, \
                f"top.v references the removed ap_memory port {dead} (AUDIT_REPORT.md §1)"

    def test_top_no_0v_suffix(self):
        text = self._read("top.v")
        assert "_0_V" not in text, \
            "top.v must NOT use _0_V port name suffixes (pre-Run-7 naming, now wrong)"


# ---------------------------------------------------------------------------
# Constraints content checks
# ---------------------------------------------------------------------------

class TestConstraints:
    def _read(self):
        return (CONST_DIR / "basys3.xdc").read_text()

    def test_clock_pin(self):
        assert "W5" in self._read(), "basys3.xdc must assign clock to W5"

    def test_clock_period(self):
        assert "10.00" in self._read(), "basys3.xdc must set 10 ns clock period (100 MHz)"

    def test_uart_rx_pin(self):
        assert "B18" in self._read(), "basys3.xdc must assign UART RX to B18"

    def test_uart_tx_pin(self):
        assert "A18" in self._read(), "basys3.xdc must assign UART TX to A18"

    def test_reset_pin(self):
        assert "U18" in self._read(), "basys3.xdc must assign reset to U18 (BTNC)"


# ---------------------------------------------------------------------------
# iverilog syntax checks (requires iverilog installed in WSL/PATH)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("iverilog") is None, reason="iverilog not installed")
class TestIverilogSyntax:
    def _check(self, *files):
        paths = [str(HDL_DIR / f) for f in files]
        result = subprocess.run(
            ["iverilog", "-g2001", "-Wall", "-o", "/dev/null"] + paths,
            capture_output=True, text=True
        )
        assert result.returncode == 0, \
            f"iverilog syntax error:\n{result.stderr}"

    def test_uart_rx_syntax(self):
        self._check("uart_rx.v")

    def test_uart_tx_syntax(self):
        self._check("uart_tx.v")

    def test_uart_framing_syntax(self):
        self._check("uart_framing.v")

    def test_mlp_controller_syntax(self):
        self._check("mlp_controller.v")

    def test_top_syntax(self):
        # top.v references myproject — use stub for syntax checking
        self._check("myproject_stub.v", "uart_rx.v", "uart_tx.v",
                    "uart_framing.v", "mlp_controller.v", "top.v")


# ---------------------------------------------------------------------------
# Synthesis report checks (only after Vivado has been run)
# ---------------------------------------------------------------------------

@pytest.mark.synthesis
class TestSynthesisReport:
    def test_report_exists(self):
        assert (ARTIFACT / "synthesis_report.json").exists(), \
            "Run Vivado synthesis then parse_reports.py to generate synthesis_report.json"

    def test_lut_within_budget(self):
        report = json.loads((ARTIFACT / "synthesis_report.json").read_text())
        lut = report["resources"]["LUT"]
        assert lut["ok"], \
            f"LUT over budget: {lut['used']} / {lut['total']} ({lut['pct']:.1f}%)"

    def test_timing_met(self):
        report = json.loads((ARTIFACT / "synthesis_report.json").read_text())
        assert report["timing"]["timing_ok"], \
            f"Timing violation: WNS = {report['timing']['WNS_ns']} ns"
