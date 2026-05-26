# Synthesis-only Tcl script for Vitis HLS.
# C simulation was already run from Python (hls_model.compile / hls_model.predict).
# This script runs C Synthesis and exports the Vivado IP catalog block only.

# project.tcl lives in hls_project/ (the CWD when vitis_hls runs), not next to this script
source project.tcl

# ── Open (or create) project ────────────────────────────────────────────────
open_project -reset ${project_name}_prj

set_top ${project_name}
add_files firmware/${project_name}.cpp -cflags "-std=c++0x"

open_solution -reset "solution1"

set_part $part
create_clock -period $clock_period -name default

# ── C Synthesis ─────────────────────────────────────────────────────────────
puts "\n=== Running C Synthesis (5-15 min) ===\n"
csynth_design

# ── Export Vivado IP catalog block (needed for Phase 5 Vivado integration) ──
puts "\n=== Exporting IP catalog ===\n"
export_design -format ip_catalog -version $version

close_project
puts "\n=== Synthesis complete. Reports in: ${project_name}_prj/solution1/syn/report/ ===\n"
