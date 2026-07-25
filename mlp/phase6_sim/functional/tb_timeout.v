// Error-path tests for mlp_controller using the stalling stub (myproject_stub_stall.v):
//   +SAT=""    : stub never produces outputs -> controller WAIT watchdog must fire
//                (result_timeout pulse, FSM returns to IDLE).            [G2]
//   +SAT=hi    : stub forces win >= 1.0  -> result_win must saturate to 0xFF.   [G4]
//   +SAT=neg   : stub forces win  < 0    -> result_win must saturate to 0x00.   [G4]
//   +SAT=mid   : stub forces win = 0.5   -> result_win = 0x80 (no saturation).  [G4]
// Small TIMEOUT_CYCLES so the timeout case is quick.
`timescale 1ns/1ps
module tb_timeout;
    reg clk = 0, rst = 1, packet_valid = 0;
    reg [167:0] feature_bus;
    wire ap_start, ap_done, ap_idle, ap_ready;
    wire [671:0] features_TDATA; wire features_TVALID, features_TREADY;
    wire [31:0] l9, l10; wire l9v, l9r, l10v, l10r;
    wire [7:0] result_win, result_spread;
    wire result_valid, result_timeout;
    integer t; reg done;
    always #5 clk = ~clk;

    mlp_controller #(.TIMEOUT_CYCLES(20'd500)) u_ctrl (
        .clk(clk), .rst(rst), .feature_bus(feature_bus), .packet_valid(packet_valid),
        .ap_start(ap_start), .ap_done(ap_done), .ap_idle(ap_idle), .ap_ready(ap_ready),
        .features_TDATA(features_TDATA), .features_TVALID(features_TVALID), .features_TREADY(features_TREADY),
        .layer9_out_TDATA(l9), .layer9_out_TVALID(l9v), .layer9_out_TREADY(l9r),
        .layer10_out_TDATA(l10), .layer10_out_TVALID(l10v), .layer10_out_TREADY(l10r),
        .result_win(result_win), .result_spread(result_spread),
        .result_valid(result_valid), .result_timeout(result_timeout));

    myproject u_mlp (   // stalling stub (mode via +SAT=)
        .ap_clk(clk), .ap_rst_n(~rst), .ap_start(ap_start), .ap_done(ap_done),
        .ap_idle(ap_idle), .ap_ready(ap_ready),
        .features_TDATA(features_TDATA), .features_TVALID(features_TVALID), .features_TREADY(features_TREADY),
        .layer9_out_TDATA(l9), .layer9_out_TVALID(l9v), .layer9_out_TREADY(l9r),
        .layer10_out_TDATA(l10), .layer10_out_TVALID(l10v), .layer10_out_TREADY(l10r));

    initial begin
        feature_bus = {21{8'h40}};
        repeat (6) @(posedge clk); rst = 0; repeat (4) @(posedge clk);
        @(posedge clk); packet_valid = 1; @(posedge clk); packet_valid = 0;
        done = 0;
        for (t = 0; t < 3000 && !done; t = t + 1) begin
            @(posedge clk);
            if (result_valid) begin
                $display("RESULT_VALID win=0x%02x spread=%0d", result_win, $signed(result_spread));
                done = 1;
            end
            if (result_timeout) begin
                $display("RESULT_TIMEOUT fired @%0d cycles", t);
                done = 1;
            end
        end
        if (!done) $display("NO OUTCOME within 3000 cycles (FAIL)");
        // controller must be back in IDLE after the outcome (not internally wedged)
        repeat (10) @(posedge clk);
        $display("post-outcome: controller ap_start=%b features_TVALID=%b", ap_start, features_TVALID);
        $finish;
    end
endmodule
