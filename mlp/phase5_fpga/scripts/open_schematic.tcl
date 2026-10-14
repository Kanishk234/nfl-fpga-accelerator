# Run in the Windows Vivado GUI Tcl Console:
# source {//wsl.localhost/Ubuntu/home/younix/nfl-fpga-accelerator/mlp/phase5_fpga/scripts/open_schematic.tcl}
# Creates a separate RTL viewing project; leaves the existing hardware build alone.
set schematic_repo [file normalize [file join [file dirname [info script]] ../../..]]
set schematic_dir "C:/nfl_mlp_schematic"
set schematic_xpr "$schematic_dir/nfl_mlp_schematic.xpr"
set schematic_hdl "$schematic_repo/mlp/phase5_fpga/hdl"
set schematic_ip "$schematic_repo/artifacts/mlp/ip_repo/hdl/verilog"

if {[current_project -quiet] ne ""} {
    error "Close the current project (File > Close Project), then source this script again."
}
foreach schematic_file [list "$schematic_ip/myproject.v" "$schematic_hdl/top.v"] {
    if {![file exists $schematic_file]} {
        error "Source missing: $schematic_file"
    }
}
if {[file exists $schematic_xpr]} {
    open_project $schematic_xpr
} else {
    create_project nfl_mlp_schematic $schematic_dir -part xc7a35tcpg236-1
    add_files -norecurse [glob "$schematic_ip/*.v"]
    add_files -norecurse [glob "$schematic_ip/*.dat"]
    foreach schematic_module {uart_rx uart_tx uart_framing mlp_controller top} {
        add_files -norecurse "$schematic_hdl/$schematic_module.v"
    }
    add_files -fileset constrs_1 -norecurse "$schematic_repo/mlp/phase5_fpga/constraints/basys3.xdc"
    set_property top top [get_filesets sources_1]
}
update_compile_order -fileset sources_1
synth_design -rtl -name rtl_1
show_schematic [get_cells {u_rx u_tx u_frame u_ctrl u_mlp_ip}]
puts "MLP RTL schematic opened. Keep the module blocks collapsed for the system overview."
