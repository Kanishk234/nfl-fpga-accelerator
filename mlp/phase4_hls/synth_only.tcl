# Synthesis Tcl script for Vitis HLS.
# C simulation was already run from Python (hls_model.compile / hls_model.predict).
# This script runs C Synthesis, RTL cosim (the io_stream verification GATE), and
# exports the Vivado IP catalog block.

# project.tcl lives in hls_project/ (the CWD when vitis_hls runs), not next to this script
source project.tcl

# ── Open (or create) project ────────────────────────────────────────────────
open_project -reset ${project_name}_prj

set_top ${project_name}
add_files firmware/${project_name}.cpp -cflags "-std=c++0x"
# Testbench + tb_data needed for cosim (the gate). Generate vectors first:
#   python mlp/phase4_hls/make_tb_data.py
add_files -tb ${project_name}_test.cpp -cflags "-std=c++0x"
# firmware/weights staged so the C reference model can load_weights_from_txt("weights/wN.txt")
# at the cosim runtime CWD. Omitting this makes cosim fail with "file weights/w2.txt does not
# exist" (a harness path issue, NOT an RTL bug). Matches hls4ml's generated build_prj.tcl.
add_files -tb firmware/weights
add_files -tb tb_data

open_solution -reset "solution1"

set_part $part
create_clock -period $clock_period -name default

# ── C Synthesis ─────────────────────────────────────────────────────────────
puts "\n=== Running C Synthesis (5-15 min) ===\n"
csynth_design

# ── RTL cosim — THE io_stream VERIFICATION GATE (see AUDIT_REPORT.md §1) ──────
# Cosim runs the real generated RTL with the real FIFOs against the C testbench.
# This is the ONLY test that catches the stream-deadlock class of bug that the
# old io_serial design hit on silicon (C-sim and the Phase-6 stubbed sim both
# missed it). If cosim hangs or fails, the RTL is broken regardless of any other
# result — do NOT export the IP. Make "cosim passes" a Phase 4 exit criterion.
puts "\n=== Running RTL cosim (verilog) -- GATE ===\n"
if {[catch {cosim_design -rtl verilog} cosim_err]} {
    puts "\n*** COSIM FAILED: $cosim_err ***"
    puts "*** Inspect the C TB log above. Common causes, in order of likelihood:"
    puts "***   1) 'file weights/wN.txt does not exist' -> harness path issue, not RTL"
    puts "***      (ensure 'add_files -tb firmware/weights' is present above)."
    puts "***   2) A stuck/never-draining hls::stream (max-depth grows unbounded) -> real"
    puts "***      RTL rate/deadlock bug; re-check DATAFLOW FIFO depths."
    puts "*** NOT exporting IP. ***"
    close_project
    exit 1
}
puts "\n=== Cosim PASSED ===\n"

# ── Export Vivado IP catalog block (needed for Phase 5 Vivado integration) ──
puts "\n=== Exporting IP catalog ===\n"
export_design -format ip_catalog -version $version

close_project
puts "\n=== Synthesis + cosim complete. Reports in: ${project_name}_prj/solution1/syn/report/ ===\n"
