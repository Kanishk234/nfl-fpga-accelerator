# build_all.tcl — batch-mode driver: project creation + full build in one run.
#   C:\AMDDesignTools\2025.2\Vivado\bin\vivado.bat -mode batch -source build_all.tcl
# (run from a Windows-local working dir so Vivado can write its journal/log)
set _here [file dirname [info script]]
source [file join $_here create_project.tcl]
source [file join $_here run_synth.tcl]
