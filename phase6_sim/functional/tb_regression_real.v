// Phase 6 functional regression — mlp_controller + the REAL io_stream myproject IP.
// Drives all 50 games' feature bytes through the controller into the real MLP and
// records the win/spread response bytes for comparison against the Python golden.
// This is the honest test the audit asked for: real RTL, no FIFO stubs, real arithmetic.
//
// Paths are passed via plusargs (+IN=, +OUT=); run vvp from the IP verilog dir so the
// ROM modules' $readmemh("./*.dat") resolve.
`timescale 1ns/1ps
module tb_regression_real;
    localparam NMAX = 50;
    localparam PER_GAME_TIMEOUT = 6000;   // IP latency ~1800 cyc; generous margin
    integer N;                            // games to run (+N=, default NMAX)

    reg clk = 0, rst = 1;
    always #5 clk = ~clk;                  // 100 MHz

    reg  [167:0] feature_bus;
    reg          packet_valid;
    wire         ap_start, ap_done, ap_idle, ap_ready;
    wire [671:0] features_TDATA; wire features_TVALID, features_TREADY;
    wire [31:0]  layer9_TDATA, layer10_TDATA;
    wire layer9_TVALID, layer9_TREADY, layer10_TVALID, layer10_TREADY;
    wire [7:0]   result_win, result_spread;
    wire         result_valid, result_timeout;

    mlp_controller u_ctrl (
        .clk(clk), .rst(rst), .feature_bus(feature_bus), .packet_valid(packet_valid),
        .ap_start(ap_start), .ap_done(ap_done), .ap_idle(ap_idle), .ap_ready(ap_ready),
        .features_TDATA(features_TDATA), .features_TVALID(features_TVALID), .features_TREADY(features_TREADY),
        .layer9_out_TDATA(layer9_TDATA), .layer9_out_TVALID(layer9_TVALID), .layer9_out_TREADY(layer9_TREADY),
        .layer10_out_TDATA(layer10_TDATA), .layer10_out_TVALID(layer10_TVALID), .layer10_out_TREADY(layer10_TREADY),
        .result_win(result_win), .result_spread(result_spread),
        .result_valid(result_valid), .result_timeout(result_timeout));

    myproject u_mlp (
        .ap_clk(clk), .ap_rst_n(~rst), .ap_start(ap_start), .ap_done(ap_done),
        .ap_idle(ap_idle), .ap_ready(ap_ready),
        .features_TDATA(features_TDATA), .features_TVALID(features_TVALID), .features_TREADY(features_TREADY),
        .layer9_out_TDATA(layer9_TDATA), .layer9_out_TVALID(layer9_TVALID), .layer9_out_TREADY(layer9_TREADY),
        .layer10_out_TDATA(layer10_TDATA), .layer10_out_TVALID(layer10_TVALID), .layer10_out_TREADY(layer10_TREADY));

    reg [7:0] feat_mem [0:NMAX*21-1];
    integer fout, g, i, t;
    reg done;
    reg [8*256-1:0] in_path, out_path;

    initial begin
        if (!$value$plusargs("IN=%s",  in_path))  in_path  = "tb_inputs.mem";
        if (!$value$plusargs("OUT=%s", out_path)) out_path = "sim_results.csv";
        if (!$value$plusargs("N=%d",   N))        N = NMAX;
        $readmemh(in_path, feat_mem);

        fout = $fopen(out_path, "w");
        $fwrite(fout, "game_idx,win_byte,spread_byte,timeout\n");

        packet_valid = 0; feature_bus = 0;
        repeat (8) @(posedge clk);
        rst = 0;
        repeat (4) @(posedge clk);

        for (g = 0; g < N; g = g + 1) begin
            // assemble feature_bus: feature i at [i*8 +: 8]
            for (i = 0; i < 21; i = i + 1)
                feature_bus[i*8 +: 8] = feat_mem[g*21 + i];

            // wait until the IP is idle, then pulse packet_valid one cycle
            while (!ap_idle) @(posedge clk);
            @(posedge clk); packet_valid = 1;
            @(posedge clk); packet_valid = 0;

            done = 0;
            for (t = 0; t < PER_GAME_TIMEOUT && !done; t = t + 1) begin
                @(posedge clk);
                if (result_valid) begin
                    $fwrite(fout, "%0d,%0d,%0d,0\n", g, result_win, result_spread);
                    $display("game %0d: win=%0d spread=%0d  (took %0d cyc, t=%0t)", g, result_win, $signed(result_spread), t, $time);
                    done = 1;
                end else if (result_timeout) begin
                    $fwrite(fout, "%0d,0,0,1\n", g);
                    $display("game %0d: TIMEOUT (controller watchdog)", g);
                    done = 1;
                end
            end
            if (!done) begin
                $fwrite(fout, "%0d,0,0,1\n", g);   // TB watchdog: never responded
                $display("game %0d: NO RESPONSE (TB watchdog after %0d cyc)", g, PER_GAME_TIMEOUT);
            end
            $fflush(fout);
        end

        $fclose(fout);
        $display("Functional regression done: %0d games written to %0s", N, out_path);
        $finish;
    end
endmodule
