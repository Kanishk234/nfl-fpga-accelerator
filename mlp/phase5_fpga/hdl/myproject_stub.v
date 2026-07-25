// Behavioral stub for the hls4ml io_stream myproject IP — used ONLY for iverilog
// syntax/elaboration checks and quick smoke sims of top.v + mlp_controller.
// This file is NOT added to the Vivado project; the real IP is imported from the
// Phase 4 export (xilinx_com_hls_myproject_1_0) via the IP catalog.
//
// Models the AXI4-Stream + ap_ctrl_hs handshake of the real IP (port names verified
// against myproject_prj/solution1/syn/verilog/myproject.v): consume one 672-bit
// features beat, then emit one beat each on layer9_out / layer10_out and pulse ap_done.
`timescale 1ns / 1ps
module myproject (
    input         ap_clk,
    input         ap_rst_n,        // active-low
    input         ap_start,
    output reg    ap_done,
    output reg    ap_idle,
    output reg    ap_ready,
    input  [671:0] features_TDATA,
    input          features_TVALID,
    output reg     features_TREADY,
    output reg [31:0] layer9_out_TDATA,
    output reg     layer9_out_TVALID,
    input          layer9_out_TREADY,
    output reg [31:0] layer10_out_TDATA,
    output reg     layer10_out_TVALID,
    input          layer10_out_TREADY
);
    localparam S_IDLE = 2'd0, S_IN = 2'd1, S_OUT = 2'd2, S_DONE = 2'd3;
    reg [1:0] st;
    reg got9, got10;

    always @(posedge ap_clk) begin
        if (!ap_rst_n) begin
            st <= S_IDLE;
            ap_done <= 1'b0; ap_idle <= 1'b1; ap_ready <= 1'b0;
            features_TREADY <= 1'b0;
            layer9_out_TVALID <= 1'b0; layer10_out_TVALID <= 1'b0;
            layer9_out_TDATA <= 32'd0; layer10_out_TDATA <= 32'd0;
            got9 <= 1'b0; got10 <= 1'b0;
        end else begin
            ap_done  <= 1'b0;
            ap_ready <= 1'b0;
            case (st)
                S_IDLE: begin
                    ap_idle <= 1'b1;
                    if (ap_start) begin
                        ap_idle  <= 1'b0;
                        ap_ready <= 1'b1;          // accepted the start
                        features_TREADY <= 1'b1;   // ready for the input beat
                        st <= S_IN;
                    end
                end
                S_IN: begin
                    if (features_TVALID && features_TREADY) begin
                        features_TREADY <= 1'b0;
                        // Dummy outputs: win ~ 0.5 (ap_fixed<18,6> = 0x008 in [11:4]=0x80 -> 128),
                        // spread ~ 3 (ap_fixed<32,16>: integer 3 at [23:16]).
                        layer9_out_TDATA  <= 32'h0000_0800;   // [11:4] = 0x80
                        layer10_out_TDATA <= 32'h0003_0000;   // [23:16] = 0x03
                        layer9_out_TVALID  <= 1'b1;
                        layer10_out_TVALID <= 1'b1;
                        got9 <= 1'b0; got10 <= 1'b0;
                        st <= S_OUT;
                    end
                end
                S_OUT: begin
                    if (layer9_out_TVALID && layer9_out_TREADY) begin
                        layer9_out_TVALID <= 1'b0; got9 <= 1'b1;
                    end
                    if (layer10_out_TVALID && layer10_out_TREADY) begin
                        layer10_out_TVALID <= 1'b0; got10 <= 1'b1;
                    end
                    if ((got9 || (layer9_out_TVALID && layer9_out_TREADY)) &&
                        (got10 || (layer10_out_TVALID && layer10_out_TREADY))) begin
                        st <= S_DONE;
                    end
                end
                S_DONE: begin
                    ap_done <= 1'b1;
                    ap_idle <= 1'b1;
                    st <= S_IDLE;
                end
                default: st <= S_IDLE;
            endcase
        end
    end
endmodule
