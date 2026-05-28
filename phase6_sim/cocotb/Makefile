SIM           ?= icarus
TOPLEVEL_LANG ?= verilog
COMPILE_ARGS  += -g2012
HDL  := $(abspath ../../phase5_fpga/hdl)
STUB := $(abspath ../../phase6_sim/stubs/myproject_stub.v)
VLOG_DIR   := $(abspath ../../phase4_hls/hls_project/myproject_prj/solution1/syn/verilog)
MLP_VLOG   := $(wildcard $(VLOG_DIR)/*.v)
# FIFO deep stubs: replace depth-2 FIFOs to prevent streaming deadlock in iverilog.
# The original d2 FIFOs have depth=2; with 128-neuron first layer, they fill before
# the consumer (sequential FSM) starts, causing a permanent stall.
FIFO_STUBS := $(abspath ../../phase6_sim/stubs/mlp_fifo_deep.v)
FIFO_ORIG  := $(VLOG_DIR)/myproject_fifo_w18_d2_S.v \
              $(VLOG_DIR)/myproject_fifo_w8_d2_S.v \
              $(VLOG_DIR)/myproject_fifo_w32_d2_S.v
# Also exclude ShiftReg helper modules (sub-modules of the original FIFOs)
FIFO_SHIFT := $(wildcard $(VLOG_DIR)/*ShiftReg*.v)
# Exclude original myproject.v — we use a patched copy that swaps layer7_out_fifo_U
# to a replay-once FIFO (config8 and config10 both read layer7_out sequentially).
MYPROJECT_V := $(VLOG_DIR)/myproject.v
MLP_VLOG_NO_FIFO := $(filter-out $(FIFO_ORIG) $(FIFO_SHIFT),$(MLP_VLOG))
MLP_VLOG_PATCHED := $(filter-out $(MYPROJECT_V),$(MLP_VLOG_NO_FIFO))
# Dual-read (replay-once) stub for layer7_out shared between win and spread heads
DUAL_FIFO_STUB := $(abspath ../../phase6_sim/stubs/mlp_fifo_dual_w8.v)
# Multi-replay stubs for rf_gt_nin layers:
#   layer3_out needs 4 passes (config4 reads 512 from 128 entries)
#   layer5_out needs 8 passes (config6 reads 512 from 64 entries)
REPLAY_STUBS := $(abspath ../../phase6_sim/stubs/mlp_fifo_replay_w8.v)

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

test_mlp_controller:
	$(MAKE) sim TOPLEVEL=mlp_controller \
	  VERILOG_SOURCES="$(HDL)/mlp_controller.v" \
	  COCOTB_TEST_MODULES=test_mlp_controller \
	  COCOTB_RESULTS_FILE=results_mlp_controller.xml SIM_BUILD=sim_build/mlp_controller

test_integration:
	$(MAKE) sim TOPLEVEL=top \
	  VERILOG_SOURCES="$(HDL)/uart_rx.v $(HDL)/uart_tx.v $(HDL)/uart_framing.v \
	                   $(HDL)/mlp_controller.v $(STUB) $(HDL)/top.v" \
	  COCOTB_TEST_MODULES=test_integration \
	  COCOTB_RESULTS_FILE=results_integration.xml SIM_BUILD=sim_build/integration

test_regression:
	$(MAKE) sim TOPLEVEL=top \
	  VERILOG_SOURCES="$(HDL)/uart_rx.v $(HDL)/uart_tx.v $(HDL)/uart_framing.v \
	                   $(HDL)/mlp_controller.v $(STUB) $(HDL)/top.v" \
	  COCOTB_TEST_MODULES=test_regression \
	  COCOTB_RESULTS_FILE=results_regression.xml SIM_BUILD=sim_build/regression

# Real hls4ml Verilog uses $readmemh with bare filenames — vvp must run from
# the verilog directory so it can find the .dat weight files at simulation time.
# We compile with iverilog normally, then cd to the verilog dir before vvp.
COCOTB_LIB := $(shell python3 -c "import cocotb, os; print(os.path.join(os.path.dirname(cocotb.__file__), 'libs'))" 2>/dev/null)
VVP_BIN := $(shell which vvp)

test_mlp_verilog: sim_build/mlp_verilog/sim.vvp
	cd $(VLOG_DIR) && \
	  COCOTB_TEST_MODULES=test_mlp_verilog \
	  COCOTB_RESULTS_FILE=$(CURDIR)/results_mlp_verilog.xml \
	  COCOTB_TOPLEVEL=myproject \
	  TOPLEVEL_LANG=verilog \
	  PYTHONPATH=$(CURDIR) \
	  $(VVP_BIN) -M $(COCOTB_LIB) -m libcocotbvpi_icarus \
	    $(CURDIR)/sim_build/mlp_verilog/sim.vvp

sim_build/mlp_verilog/myproject_patched.v: $(MYPROJECT_V)
	mkdir -p sim_build/mlp_verilog
	sed -e 's/myproject_fifo_w8_d2_S layer3_out_fifo_U/myproject_fifo_w8_d2_S_replay4 layer3_out_fifo_U/' \
	    -e 's/myproject_fifo_w8_d2_S layer5_out_fifo_U/myproject_fifo_w8_d2_S_replay8 layer5_out_fifo_U/' \
	    -e 's/myproject_fifo_w8_d2_S layer7_out_fifo_U/myproject_fifo_w8_d2_S_dual layer7_out_fifo_U/' \
	    $< > $@

sim_build/mlp_verilog/sim.vvp: $(MLP_VLOG_PATCHED) sim_build/mlp_verilog/myproject_patched.v $(FIFO_STUBS) $(DUAL_FIFO_STUB) $(REPLAY_STUBS)
	mkdir -p sim_build/mlp_verilog
	/usr/bin/iverilog -o $@ -s myproject -g2012 \
	  $(MLP_VLOG_PATCHED) sim_build/mlp_verilog/myproject_patched.v \
	  $(FIFO_STUBS) $(DUAL_FIFO_STUB) $(REPLAY_STUBS)

all: test_uart_rx test_uart_tx test_uart_framing test_mlp_controller \
     test_integration test_regression
# Note: test_mlp_verilog runs separately — real Verilog arithmetic check

include $(shell cocotb-config --makefiles)/Makefile.sim
