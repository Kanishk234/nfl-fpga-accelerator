# Driver for conifer's generated build_hls.tcl — sets the option flags then
# delegates. conifer parses ::argv for name=value pairs (see build_hls.tcl).
#
# cosim=1: the RTL cosim gate — same policy as the MLP flow (synth_only.tcl):
# if cosim fails, the RTL is broken regardless of csynth results.
# export=1: emit the IP catalog block for phase-5 Vivado integration.
set ::argv [list reset=1 csim=1 synth=1 cosim=1 export=1]
source build_hls.tcl
