# run_synth.tcl (conifer/GBDT variant) — synthesis, implementation, bitstream.
# Identical flow to mlp/phase5_fpga/scripts/run_synth.tcl (incl. the multi-driven-net
# guard); only the bitstream name differs (top_gbdt.bit).
# Run from the Vivado Tcl console AFTER create_project.tcl has been sourced.

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
# GUARD: abort on multi-driven nets (AUDIT_REPORT.md §3)
# -----------------------------------------------------------------------
set _synth_dir [get_property DIRECTORY [get_runs synth_1]]
set _logf [file join $_synth_dir "runme.log"]
if {[file exists $_logf]} {
    set _fh [open $_logf r]; set _txt [read $_fh]; close $_fh
    if {[string match -nocase "*multi-driven*" $_txt]} {
        error "Multi-driven net found in synthesis log:\n  $_logf\nFix it before continuing\
               — Vivado may have kept a constant driver and swept real logic (AUDIT_REPORT.md §3)."
    }
    puts "Multi-driven-net check OK (none found in synthesis log)."
}

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

set bit_file [get_property DIRECTORY [get_runs impl_1]]/top_gbdt.bit
puts "======================================================="
puts "Bitstream: $bit_file"
puts "Utilization: $REPORT_DIR/utilization_report.txt"
puts "Timing:      $REPORT_DIR/timing_report.txt"
puts "======================================================="
