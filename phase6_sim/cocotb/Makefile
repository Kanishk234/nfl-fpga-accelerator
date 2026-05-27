# cocotb Makefile for NFL FPGA Accelerator Phase 6 simulation
# Simulator: Icarus Verilog (iverilog), cocotb 2.x
# Run individual targets: make uart_rx, make uart_tx, etc.
# Run all:               make all_tests

SIM           ?= icarus
TOPLEVEL_LANG ?= verilog
COMPILE_ARGS  += -g2012

VERILOG_SOURCES_BASE = $(PWD)/../../phase5_fpga/hdl

# --- uart_rx unit test ---
uart_rx:
	$(MAKE) sim \
	  TOPLEVEL=uart_rx \
	  VERILOG_SOURCES="$(VERILOG_SOURCES_BASE)/uart_rx.v" \
	  MODULE=test_uart_rx \
	  COCOTB_RESULTS_FILE=results_uart_rx.xml \
	  SIM_BUILD=sim_build/uart_rx

# --- uart_tx unit test ---
uart_tx:
	$(MAKE) sim \
	  TOPLEVEL=uart_tx \
	  VERILOG_SOURCES="$(VERILOG_SOURCES_BASE)/uart_tx.v" \
	  MODULE=test_uart_tx \
	  COCOTB_RESULTS_FILE=results_uart_tx.xml \
	  SIM_BUILD=sim_build/uart_tx

# --- uart_framing unit test ---
uart_framing:
	$(MAKE) sim \
	  TOPLEVEL=uart_framing \
	  VERILOG_SOURCES="$(VERILOG_SOURCES_BASE)/uart_rx.v \
	                   $(VERILOG_SOURCES_BASE)/uart_tx.v \
	                   $(VERILOG_SOURCES_BASE)/uart_framing.v" \
	  MODULE=test_uart_framing \
	  COCOTB_RESULTS_FILE=results_uart_framing.xml \
	  SIM_BUILD=sim_build/uart_framing

# --- mlp_controller unit test ---
mlp_controller:
	$(MAKE) sim \
	  TOPLEVEL=mlp_controller \
	  VERILOG_SOURCES="$(VERILOG_SOURCES_BASE)/mlp_controller.v" \
	  MODULE=test_mlp_controller \
	  COCOTB_RESULTS_FILE=results_mlp_controller.xml \
	  SIM_BUILD=sim_build/mlp_controller

# --- integration test (fast stub MLP) ---
integration:
	$(MAKE) sim \
	  TOPLEVEL=top \
	  VERILOG_SOURCES="$(VERILOG_SOURCES_BASE)/uart_rx.v \
	                   $(VERILOG_SOURCES_BASE)/uart_tx.v \
	                   $(VERILOG_SOURCES_BASE)/uart_framing.v \
	                   $(VERILOG_SOURCES_BASE)/mlp_controller.v \
	                   $(VERILOG_SOURCES_BASE)/myproject_stub_fast.v \
	                   $(VERILOG_SOURCES_BASE)/top.v" \
	  MODULE=test_top_integration \
	  COCOTB_RESULTS_FILE=results_integration.xml \
	  SIM_BUILD=sim_build/integration

# --- run all tests in order ---
all_tests: uart_rx uart_tx uart_framing mlp_controller integration

include $(shell cocotb-config --makefiles)/Makefile.sim
