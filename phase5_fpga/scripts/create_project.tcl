# create_project.tcl — Creates the Vivado project for the NFL FPGA Accelerator.
# Run from the Vivado Tcl console on Windows:
#   source C:/path/to/phase5_fpga/scripts/create_project.tcl
#
# Adjust the three path variables below to match your machine.

# -----------------------------------------------------------------------
# USER-CONFIGURABLE PATHS (edit these before running)
# -----------------------------------------------------------------------
# Windows path to the nfl-fpga-accelerator repo root (use forward slashes)
set REPO_ROOT   "C:/Users/kanis/nfl-fpga-accelerator"

# Where Vivado should create the project (must not be inside the repo)
set PROJECT_DIR "C:/nfl_fpga_build"

# -----------------------------------------------------------------------
# DERIVED PATHS (do not edit)
# -----------------------------------------------------------------------
set PROJECT_NAME nfl_fpga_accelerator
set HDL_DIR      "$REPO_ROOT/phase5_fpga/hdl"
set XDC_FILE     "$REPO_ROOT/phase5_fpga/constraints/basys3.xdc"
set IP_ZIP       "$REPO_ROOT/artifacts/xilinx_com_hls_myproject_1_0.zip"
set IP_REPO_DIR  "$PROJECT_DIR/ip_repo"

# -----------------------------------------------------------------------
# PROJECT CREATION
# -----------------------------------------------------------------------
create_project $PROJECT_NAME $PROJECT_DIR -part xc7a35tcpg236-1 -force

# -----------------------------------------------------------------------
# ADD IP FROM PHASE 4
# Import the hls4ml IP zip into a local IP repository directory.
# -----------------------------------------------------------------------
file mkdir $IP_REPO_DIR
exec unzip -o $IP_ZIP -d $IP_REPO_DIR
set_property ip_repo_paths $IP_REPO_DIR [current_project]
update_ip_catalog -rebuild

# -----------------------------------------------------------------------
# ADD HDL SOURCES
# myproject_stub.v is NOT added — the real IP block is used instead.
# -----------------------------------------------------------------------
add_files -norecurse [list \
    $HDL_DIR/uart_rx.v \
    $HDL_DIR/uart_tx.v \
    $HDL_DIR/uart_framing.v \
    $HDL_DIR/mlp_controller.v \
    $HDL_DIR/top.v \
]
set_property top top [current_fileset]

# -----------------------------------------------------------------------
# ADD CONSTRAINTS
# -----------------------------------------------------------------------
add_files -fileset constrs_1 -norecurse $XDC_FILE

# -----------------------------------------------------------------------
# SYNTHESIS STRATEGY
# -----------------------------------------------------------------------
set_property strategy Flow_PerfOptimized_high [get_runs synth_1]

puts "======================================================="
puts "Project created at: $PROJECT_DIR/$PROJECT_NAME.xpr"
puts "Next: source run_synth.tcl, or open the project in Vivado GUI."
puts "======================================================="
