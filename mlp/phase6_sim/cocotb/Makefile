# cocotb UART-module unit tests (iverilog backend).
#
# SCOPE: ONLY the UART leaf modules (uart_rx / uart_tx / uart_framing), which are small
# and stable. The MLP/controller/integration/regression cocotb tests + FIFO stubs were
# REMOVED in the post-audit cleanup: they targeted the old io_serial ap_memory IP and
# masked the in-hardware deadlock by substituting fake replay FIFOs (AUDIT_REPORT.md §1).
# The honest real-IP verification now lives in ../functional/ (XSIM). See ../PHASE6_COMPLETE.md.
SIM           ?= icarus
TOPLEVEL_LANG ?= verilog
COMPILE_ARGS  += -g2012
HDL := $(abspath ../../phase5_fpga/hdl)

test_uart_rx:
	$(MAKE) sim TOPLEVEL=uart_rx \
	  VERILOG_SOURCES="$(HDL)/uart_rx.v" \
	  COCOTB_TEST_MODULES=test_uart_rx \
	  COCOTB_RESULTS_FILE=results_uart_rx.xml SIM_BUILD=sim_build/uart_rx

test_uart_tx:
	$(MAKE) sim TOPLEVEL=uart_tx \
	  VERILOG_SOURCES="$(HDL)/uart_tx.v" \
	  COCOTB_TEST_MODULES=test_uart_tx \
	  COCOTB_RESULTS_FILE=results_uart_tx.xml SIM_BUILD=sim_build/uart_tx

test_uart_framing:
	$(MAKE) sim TOPLEVEL=uart_framing \
	  VERILOG_SOURCES="$(HDL)/uart_rx.v $(HDL)/uart_tx.v $(HDL)/uart_framing.v" \
	  COCOTB_TEST_MODULES=test_uart_framing \
	  COCOTB_RESULTS_FILE=results_uart_framing.xml SIM_BUILD=sim_build/uart_framing

all: test_uart_rx test_uart_tx test_uart_framing

include $(shell cocotb-config --makefiles)/Makefile.sim
