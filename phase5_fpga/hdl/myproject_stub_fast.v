// Fast stub for hls4ml myproject IP — simulation only, NOT added to Vivado.
// Returns fixed outputs after LATENCY_CYCLES clocks to enable fast integration testing.
// The real MLP IP takes ~1,759 cycles; using 20 here keeps cocotb tests in seconds.
`timescale 1ns / 1ps
module myproject (
    input  wire        ap_clk,
    input  wire        ap_rst,
    input  wire        ap_start,
    output reg         ap_done,
    output reg         ap_idle,
    output reg         ap_ready,
    output reg  [4:0]  features_address0,
    output reg         features_ce0,
    input  wire [17:0] features_q0,
    output reg  [17:0] layer9_out,
    output reg         layer9_out_ap_vld,
    output reg  [31:0] layer10_out,
    output reg         layer10_out_ap_vld
);
    parameter LATENCY_CYCLES = 20;

    // Fixed return values: win ~75% (bit[11:4]=0xC0), spread -5 (bit[23:16]=0xFB)
    // 18'h00C00 = 3072: ap_fixed<18,6> value 0.75 → bits[11:4]=0xC0
    parameter [17:0] WIN_OUT    = 18'h00C00;
    parameter [31:0] SPREAD_OUT = 32'hFFFB0000;

    integer counter;
    reg running;

    initial begin
        ap_done            = 0;
        ap_idle            = 1;
        ap_ready           = 1;
        layer9_out         = 0;
        layer9_out_ap_vld  = 0;
        layer10_out        = 0;
        layer10_out_ap_vld = 0;
        features_address0  = 0;
        features_ce0       = 0;
        counter            = 0;
        running            = 0;
    end

    always @(posedge ap_clk) begin
        ap_done            <= 0;
        layer9_out_ap_vld  <= 0;
        layer10_out_ap_vld <= 0;
        features_ce0       <= 0;

        if (ap_rst) begin
            running  <= 0;
            counter  <= 0;
            ap_idle  <= 1;
            ap_ready <= 1;
        end else if (ap_start && !running) begin
            running  <= 1;
            counter  <= 0;
            ap_idle  <= 0;
            ap_ready <= 0;
        end else if (running) begin
            counter <= counter + 1;
            // Read features sequentially during simulated inference
            if (counter < 21) begin
                features_ce0      <= 1;
                features_address0 <= counter[4:0];
            end
            if (counter == LATENCY_CYCLES - 1) begin
                layer9_out         <= WIN_OUT;
                layer9_out_ap_vld  <= 1;
                layer10_out        <= SPREAD_OUT;
                layer10_out_ap_vld <= 1;
                ap_done            <= 1;
                ap_idle            <= 1;
                ap_ready           <= 1;
                running            <= 0;
                counter            <= 0;
            end
        end
    end
endmodule
