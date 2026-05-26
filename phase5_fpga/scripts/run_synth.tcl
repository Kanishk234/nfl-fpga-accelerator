# run_synth.tcl — Runs synthesis, implementation, and bitstream generation.
# Run from the Vivado Tcl console AFTER create_project.tcl has been sourced:
#   source C:/path/to/phase5_fpga/scripts/run_synth.tcl
#
# Reports are written next to the script; copy them to WSL and run parse_reports.py.

set REPORT_DIR [file dirname [info script]]

# -----------------------------------------------------------------------
# SYNTHESIS
# -----------------------------------------------------------------------
launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] != "100%"} {
    error "Synthesis failed — check the Messages panel for errors."
}
puts "Synthesis complete."

# -----------------------------------------------------------------------
# IMPLEMENTATION (place and route)
# -----------------------------------------------------------------------
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] != "100%"} {
    error "Implementation failed — check the Messages panel for errors."
}
puts "Implementation complete."

# -----------------------------------------------------------------------
# REPORTS
# -----------------------------------------------------------------------
open_run impl_1
report_utilization    -file "$REPORT_DIR/utilization_report.txt"
report_timing_summary -file "$REPORT_DIR/timing_report.txt" -warn_on_violation

set bit_file [get_property DIRECTORY [get_runs impl_1]]/top.bit
puts "======================================================="
puts "Bitstream: $bit_file"
puts "Utilization: $REPORT_DIR/utilization_report.txt"
puts "Timing:      $REPORT_DIR/timing_report.txt"
puts ""
puts "Copy report files to WSL and run:"
puts "  python3 phase5_fpga/scripts/parse_reports.py"
puts "======================================================="
