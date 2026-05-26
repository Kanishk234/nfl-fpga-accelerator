// Stub for hls4ml myproject IP — used ONLY for iverilog syntax checking of top.v.
// This file is NOT added to the Vivado project; the real IP is imported from
// artifacts/xilinx_com_hls_myproject_1_0.zip via the IP catalog.
`timescale 1ns / 1ps
module myproject (
    input         ap_clk,
    input         ap_rst,
    input         ap_start,
    output        ap_done,
    output        ap_idle,
    output        ap_ready,
    output [4:0]  features_address0,
    output        features_ce0,
    input  [17:0] features_q0,
    output [17:0] layer9_out,
    output        layer9_out_ap_vld,
    output [31:0] layer10_out,
    output        layer10_out_ap_vld
);
    assign ap_done            = 1'b0;
    assign ap_idle            = 1'b1;
    assign ap_ready           = 1'b1;
    assign features_address0  = 5'd0;
    assign features_ce0       = 1'b0;
    assign layer9_out         = 18'd0;
    assign layer9_out_ap_vld  = 1'b0;
    assign layer10_out        = 32'd0;
    assign layer10_out_ap_vld = 1'b0;
endmodule
