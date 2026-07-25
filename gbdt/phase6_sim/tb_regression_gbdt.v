// Phase 6 (conifer) functional regression — gbdt_controller + sigmoid ROM + the
// REAL conifer_win / conifer_spread netlists, driven over 100 val games; the
// full-width fixed-point results are recorded for bit-exact comparison against
// chain_golden/ (check_results.py). Mirrors phase6_sim/functional/tb_regression_real.v.
//
// Inputs come from chain_golden/tb_inputs.mem: 24-bit two's-complement hex,
// game-major, feature 0 first — exactly what the UART protocol carries.
`timescale 1ns/1ps
module tb_regression_gbdt;
    localparam NMAX = 100;
    localparam PER_GAME_TIMEOUT = 1000;   // real latency ~25 cyc; generous margin
    integer N;                            // games to run (+N=, default NMAX)

    reg clk = 0, rst = 1;
    always #5 clk = ~clk;                 // 100 MHz

    reg  [503:0] feature_bus;
    reg          packet_valid;
    wire [503:0] x_bus;
    wire [23:0]  win_prob;
    wire         win_ap_start, win_ap_done, win_ap_idle, win_ap_ready;
    wire [23:0]  win_score;
    wire         win_score_vld;
    wire         spread_ap_start, spread_ap_done, spread_ap_idle, spread_ap_ready;
    wire [23:0]  spread_score;
    wire         spread_score_vld;
    wire [9:0]   sig_addr;
    wire [11:0]  sig_data;
    wire [23:0]  result_win_prob, result_spread;
    wire         result_valid, result_timeout;

    gbdt_controller u_ctrl (
        .clk(clk), .rst(rst), .feature_bus(feature_bus), .packet_valid(packet_valid),
        .x_bus(x_bus), .win_prob(win_prob),
        .win_ap_start(win_ap_start), .win_ap_done(win_ap_done),
        .win_ap_idle(win_ap_idle), .win_ap_ready(win_ap_ready),
        .win_score(win_score), .win_score_vld(win_score_vld),
        .spread_ap_start(spread_ap_start), .spread_ap_done(spread_ap_done),
        .spread_ap_idle(spread_ap_idle), .spread_ap_ready(spread_ap_ready),
        .spread_score(spread_score), .spread_score_vld(spread_score_vld),
        .sig_addr(sig_addr), .sig_data(sig_data),
        .result_win_prob(result_win_prob), .result_spread(result_spread),
        .result_valid(result_valid), .result_timeout(result_timeout));

    sigmoid_rom u_sigmoid (.clk(clk), .addr(sig_addr), .data(sig_data));

    conifer_win u_win_ip (
        .ap_clk(clk), .ap_rst(rst),
        .ap_start(win_ap_start), .ap_done(win_ap_done),
        .ap_idle(win_ap_idle), .ap_ready(win_ap_ready),
        .x_0(x_bus[0*24 +: 24]),   .x_1(x_bus[1*24 +: 24]),   .x_2(x_bus[2*24 +: 24]),
        .x_3(x_bus[3*24 +: 24]),   .x_4(x_bus[4*24 +: 24]),   .x_5(x_bus[5*24 +: 24]),
        .x_6(x_bus[6*24 +: 24]),   .x_7(x_bus[7*24 +: 24]),   .x_8(x_bus[8*24 +: 24]),
        .x_9(x_bus[9*24 +: 24]),   .x_10(x_bus[10*24 +: 24]), .x_11(x_bus[11*24 +: 24]),
        .x_12(x_bus[12*24 +: 24]), .x_13(x_bus[13*24 +: 24]), .x_14(x_bus[14*24 +: 24]),
        .x_15(x_bus[15*24 +: 24]), .x_16(x_bus[16*24 +: 24]), .x_17(x_bus[17*24 +: 24]),
        .x_18(x_bus[18*24 +: 24]), .x_19(x_bus[19*24 +: 24]), .x_20(x_bus[20*24 +: 24]),
        .score_0(win_score), .score_0_ap_vld(win_score_vld), .score_1(24'd0));

    conifer_spread u_spread_ip (
        .ap_clk(clk), .ap_rst(rst),
        .ap_start(spread_ap_start), .ap_done(spread_ap_done),
        .ap_idle(spread_ap_idle), .ap_ready(spread_ap_ready),
        .x_0(x_bus[0*24 +: 24]),   .x_1(x_bus[1*24 +: 24]),   .x_2(x_bus[2*24 +: 24]),
        .x_3(x_bus[3*24 +: 24]),   .x_4(x_bus[4*24 +: 24]),   .x_5(x_bus[5*24 +: 24]),
        .x_6(x_bus[6*24 +: 24]),   .x_7(x_bus[7*24 +: 24]),   .x_8(x_bus[8*24 +: 24]),
        .x_9(x_bus[9*24 +: 24]),   .x_10(x_bus[10*24 +: 24]), .x_11(x_bus[11*24 +: 24]),
        .x_12(x_bus[12*24 +: 24]), .x_13(x_bus[13*24 +: 24]), .x_14(x_bus[14*24 +: 24]),
        .x_15(x_bus[15*24 +: 24]), .x_16(x_bus[16*24 +: 24]), .x_17(x_bus[17*24 +: 24]),
        .x_18(x_bus[18*24 +: 24]), .x_19(x_bus[19*24 +: 24]), .x_20(x_bus[20*24 +: 24]),
        .x_21(win_prob),
        .score_0(spread_score), .score_0_ap_vld(spread_score_vld), .score_1(24'd0));

    reg [23:0] feat_mem [0:NMAX*21-1];
    integer fout, g, i, t;
    reg done;
    reg [8*256-1:0] in_path, out_path;

    initial begin
        if (!$value$plusargs("IN=%s",  in_path))  in_path  = "tb_inputs.mem";
        if (!$value$plusargs("OUT=%s", out_path)) out_path = "sim_results.csv";
        if (!$value$plusargs("N=%d",   N))        N = NMAX;
        $readmemh(in_path, feat_mem);

        fout = $fopen(out_path, "w");
        $fwrite(fout, "game_idx,win_prob_fixed,spread_fixed,timeout\n");

        packet_valid = 0; feature_bus = 0;
        repeat (8) @(posedge clk);
        rst = 0;
        repeat (4) @(posedge clk);

        for (g = 0; g < N; g = g + 1) begin
            for (i = 0; i < 21; i = i + 1)
                feature_bus[i*24 +: 24] = feat_mem[g*21 + i];

            // wait until both IPs are idle, then pulse packet_valid one cycle
            while (!(win_ap_idle && spread_ap_idle)) @(posedge clk);
            @(posedge clk); packet_valid = 1;
            @(posedge clk); packet_valid = 0;

            done = 0;
            for (t = 0; t < PER_GAME_TIMEOUT && !done; t = t + 1) begin
                @(posedge clk);
                if (result_valid) begin
                    // win_prob is unsigned [0,1); spread is signed — write raw
                    // fixed-point integers, the checker handles two's complement.
                    $fwrite(fout, "%0d,%0d,%0d,0\n", g, result_win_prob, result_spread);
                    $display("game %0d: prob=%0d spread=%0d  (took %0d cyc)",
                             g, result_win_prob, $signed(result_spread), t);
                    done = 1;
                end else if (result_timeout) begin
                    $fwrite(fout, "%0d,0,0,1\n", g);
                    $display("game %0d: TIMEOUT (controller watchdog)", g);
                    done = 1;
                end
            end
            if (!done) begin
                $fwrite(fout, "%0d,0,0,1\n", g);
                $display("game %0d: NO RESPONSE (TB watchdog after %0d cyc)", g, PER_GAME_TIMEOUT);
            end
            $fflush(fout);
        end

        $fclose(fout);
        $display("GBDT chain regression done: %0d games written to %0s", N, out_path);
        $finish;
    end
endmodule
