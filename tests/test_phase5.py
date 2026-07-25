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
        # byte_cnt goes up to 20 (0..20 = 21 features)
        assert "5'd20" in text or "20" in text, \
            "uart_framing.v must collect exactly 21 features (byte_cnt 0-20)"

    def test_uart_framing_feature_bus_width(self):
        text = self._read("uart_framing.v")
        assert "167:0" in text, \
            "uart_framing.v must have 168-bit feature_bus (21 × 8 bits)"

    def test_uart_framing_xor_checksum(self):
        text = self._read("uart_framing.v")
        assert "checksum ^ rx_data" in text or "^ rx_data" in text, \
            "uart_framing.v must compute XOR checksum"

    def test_mlp_controller_ap_start_combinational(self):
        text = self._read("mlp_controller.v")
        assert "assign ap_start" in text, \
            "mlp_controller.v must drive ap_start as a combinational assign"

    def test_mlp_controller_features_q0_encoding(self):
        text = self._read("mlp_controller.v")
        # encoding: {6'b0, byte, 4'b0}
        assert "4'b0}" in text or "4'b0" in text, \
            "mlp_controller.v must encode features_q0 as {6'b0, byte, 4'b0}"

    def test_mlp_controller_win_bit_extract(self):
        text = self._read("mlp_controller.v")
        assert "11:4" in text, \
            "mlp_controller.v must extract win prob from layer9_out[11:4]"

    def test_mlp_controller_spread_bit_extract(self):
        text = self._read("mlp_controller.v")
        assert "23:16" in text, \
            "mlp_controller.v must extract spread from layer10_out[23:16]"

    def test_top_instantiates_myproject(self):
        text = self._read("top.v")
        assert "myproject" in text, \
            "top.v must instantiate the myproject hls4ml IP"

    def test_top_port_names(self):
        text = self._read("top.v")
        for port in ["layer9_out_ap_vld", "layer10_out_ap_vld",
                     "features_address0", "features_ce0", "features_q0"]:
            assert port in text, f"top.v is missing port {port}"

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
