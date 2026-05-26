# create_project.tcl — Creates the Vivado project for the NFL FPGA Accelerator.
# Run from the Vivado Tcl console on Windows:
#   source {//wsl.localhost/Ubuntu/home/younix/nfl-fpga-accelerator/phase5_fpga/scripts/create_project.tcl}
#
# REPO_ROOT points to the WSL repo via the Windows UNC path.
# The IP zip has already been extracted to artifacts/ip_repo/ in WSL — no unzip needed here.

# -----------------------------------------------------------------------
# USER-CONFIGURABLE PATHS
# -----------------------------------------------------------------------
# WSL repo root — accessible from Windows Vivado via UNC path (forward slashes)
set REPO_ROOT   "//wsl.localhost/Ubuntu/home/younix/nfl-fpga-accelerator"

# Where Vivado creates the project — must be a Windows-native path (not WSL)
set PROJECT_DIR "C:/nfl_fpga_build"

# -----------------------------------------------------------------------
# DERIVED PATHS (do not edit)
# -----------------------------------------------------------------------
set PROJECT_NAME nfl_fpga_accelerator
set HDL_DIR      "$REPO_ROOT/phase5_fpga/hdl"
set XDC_FILE     "$REPO_ROOT/phase5_fpga/constraints/basys3.xdc"
set IP_REPO_DIR  "$REPO_ROOT/artifacts/ip_repo"

# -----------------------------------------------------------------------
# PROJECT CREATION
# -----------------------------------------------------------------------
create_project $PROJECT_NAME $PROJECT_DIR -part xc7a35tcpg236-1 -force

# -----------------------------------------------------------------------
# ADD MLP IP SOURCE FILES DIRECTLY
# The IP was pre-extracted from artifacts/xilinx_com_hls_myproject_1_0.zip
# into artifacts/ip_repo/. Add the Verilog files directly — this avoids
# the IP catalog output-product-generation step that causes "module not found".
# The .dat files (ROM init data) are added alongside the .v files so
# synthesis can locate them via $readmemh.
# -----------------------------------------------------------------------
add_files -norecurse [glob $IP_REPO_DIR/hdl/verilog/*.v]
add_files -norecurse [glob $IP_REPO_DIR/hdl/verilog/*.dat]

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
