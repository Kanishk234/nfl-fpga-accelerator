# create_project.tcl (conifer/GBDT variant) — Creates the Vivado project for the
# two-stage GBDT accelerator. Adapted from phase5_fpga/scripts/create_project.tcl.
# Run from the Vivado Tcl console on Windows:
#   source {//wsl.localhost/Ubuntu/home/younix/nfl-fpga-accelerator/phase5_fpga_conifer/scripts/create_project.tcl}
#
# Sources come from three places:
#   - both conifer IP netlists (phase4_conifer/hls_*/…/syn/verilog — gitignored,
#     regenerate with convert_hls.py + run_synthesis.bat if missing)
#   - this phase's wrapper HDL (hdl/)
#   - unchanged UART primitives from phase5_fpga/hdl/ (single source of truth)

# -----------------------------------------------------------------------
# USER-CONFIGURABLE PATHS
# -----------------------------------------------------------------------
set REPO_ROOT   "//wsl.localhost/Ubuntu/home/younix/nfl-fpga-accelerator"

# Where Vivado creates the project — must be a Windows-native path (not WSL)
set PROJECT_DIR "C:/nfl_gbdt_build"

# -----------------------------------------------------------------------
# DERIVED PATHS (do not edit)
# -----------------------------------------------------------------------
set PROJECT_NAME nfl_gbdt_accelerator
set HDL_DIR      "$REPO_ROOT/phase5_fpga_conifer/hdl"
set MLP_HDL_DIR  "$REPO_ROOT/phase5_fpga/hdl"
set XDC_FILE     "$REPO_ROOT/phase5_fpga/constraints/basys3.xdc"
set WIN_IP_DIR   "$REPO_ROOT/phase4_conifer/hls_win/conifer_win/solution1/syn/verilog"
set SPREAD_IP_DIR "$REPO_ROOT/phase4_conifer/hls_spread/conifer_spread/solution1/syn/verilog"
set SIGMOID_MEM  "$REPO_ROOT/phase6_sim_conifer/chain_golden/sigmoid_lut.mem"

# -----------------------------------------------------------------------
# GUARDS — refuse to build without both IP netlists and the sigmoid ROM data
# -----------------------------------------------------------------------
foreach {what f} [list \
    "conifer_win netlist"    "$WIN_IP_DIR/conifer_win.v" \
    "conifer_spread netlist" "$SPREAD_IP_DIR/conifer_spread.v" \
    "sigmoid ROM data"       "$SIGMOID_MEM" \
] {
    if {![file exists $f]} {
        error "$what not found at $f — regenerate (convert_hls.py + run_synthesis.bat\
               for IPs, make_chain_golden.py for the ROM) before creating the project."
    }
}
puts "Source checks OK: both conifer netlists + sigmoid_lut.mem present."

# -----------------------------------------------------------------------
# PROJECT CREATION
# -----------------------------------------------------------------------
create_project $PROJECT_NAME $PROJECT_DIR -part xc7a35tcpg236-1 -force

# Both IP netlists are plain Verilog with unique conifer_win_*/conifer_spread_*
# module prefixes — no name clashes, added directly (no IP catalog step).
add_files -norecurse [glob $WIN_IP_DIR/*.v]
add_files -norecurse [glob $SPREAD_IP_DIR/*.v]

# Wrapper HDL + unchanged UART primitives. sigmoid_lut.mem is added as a
# source so synthesis and simulation can find it via $readmemh.
add_files -norecurse [list \
    $MLP_HDL_DIR/uart_rx.v \
    $MLP_HDL_DIR/uart_tx.v \
    $HDL_DIR/uart_framing_conifer.v \
    $HDL_DIR/gbdt_controller.v \
    $HDL_DIR/sigmoid_rom.v \
    $HDL_DIR/top_gbdt.v \
    $SIGMOID_MEM \
]
set_property top top_gbdt [current_fileset]

# -----------------------------------------------------------------------
# ADD CONSTRAINTS — Basys 3 pinout is identical to the MLP design (reused)
# -----------------------------------------------------------------------
add_files -fileset constrs_1 -norecurse $XDC_FILE

# -----------------------------------------------------------------------
# SYNTHESIS STRATEGY
# -----------------------------------------------------------------------
set_property strategy Flow_PerfOptimized_high [get_runs synth_1]

puts "======================================================="
puts "Project created at: $PROJECT_DIR/$PROJECT_NAME.xpr"
puts "Next: source phase5_fpga_conifer/scripts/run_synth.tcl"
puts "======================================================="
