// Phase 6 (conifer) full-chain UART test — drives the ENTIRE `top_gbdt` over
// real UART at 115200 baud: serialize the 65-byte request into uart_rxd, let
// RX -> framing -> controller -> both real IPs -> framing TX run, deserialize
// the 8-byte response, and check it BIT-EXACTLY against golden_fixed.mem.
// Adapted from phase6_sim/functional/tb_top_uart.v (MLP); same receiver
// strategy: lock to the SOF start edge once, then sample all frames at fixed
// offsets (robust against mostly-low bytes).
//
// Scenarios: NG good packets (bit-exact values + status 0x00), one corrupted
// checksum (NACK status 0x01), one SOF-collision packet (every feature byte
// 0xAA — framing must treat them as data, not a new SOF).
`timescale 1ns/1ps
module tb_top_uart_gbdt;
    localparam integer CPB = 868;          // CLKS_PER_BIT = 100e6 / 115200
    localparam integer NG  = 3;            // games driven over UART
    localparam integer NFB = 63;           // feature bytes per packet (21 x 3)
    // Receiver arm window: the DUT answers only after the whole 65-byte request
    // (~565k cycles at 8,680 cycles/byte) + 22 cycles compute. Must outlast it.
    localparam integer RXWAIT = 800000;

    reg clk = 0, rst = 1;
    reg uart_rxd = 1;                       // idle high
    wire uart_txd;
    wire [2:0] led;
    always #5 clk = ~clk;                   // 100 MHz

    top_gbdt u_top (.clk(clk), .rst(rst), .uart_rxd(uart_rxd), .uart_txd(uart_txd), .led(led));

    reg [23:0] feat_mem   [0:100*21-1];     // chain_golden/tb_inputs.mem
    reg [23:0] golden_mem [0:100*2-1];      // chain_golden/golden_fixed.mem (prob, spread per game)
    reg [7:0]  fb [0:NFB-1];                // serialized feature bytes for one packet
    reg [7:0]  resp [0:7];
    reg [7:0]  csum;
    reg [23:0] exp_prob, exp_spread, got_prob, got_spread;
    integer g, i, fails;
    reg recv_ok;

    task automatic uart_send(input [7:0] b);
        integer k;
        begin
            uart_rxd = 1'b0; repeat (CPB) @(posedge clk);            // start
            for (k = 0; k < 8; k = k + 1) begin
                uart_rxd = b[k]; repeat (CPB) @(posedge clk);
            end
            uart_rxd = 1'b1; repeat (CPB) @(posedge clk);            // stop
        end
    endtask

    // Receive the 8-byte response into resp[]. Lock to the SOF start edge once,
    // then sample the 8 contiguous 10-bit frames at fixed offsets.
    task automatic uart_recv8(output okf, input integer maxwait);
        integer bytei, k, guard;
        reg [7:0] bb;
        begin
            okf = 1'b1;
            for (bytei = 0; bytei < 8; bytei = bytei + 1) resp[bytei] = 8'h00;
            guard = 0;                                               // ensure idle high
            while (uart_txd !== 1'b1 && guard < maxwait) begin @(posedge clk); guard = guard + 1; end
            guard = 0;                                               // wait for SOF start (low)
            while (uart_txd !== 1'b0 && guard < maxwait) begin @(posedge clk); guard = guard + 1; end
            if (guard >= maxwait) okf = 1'b0;
            else begin
                for (bytei = 0; bytei < 8; bytei = bytei + 1) begin
                    repeat (CPB + CPB/2) @(posedge clk);             // -> middle of this frame's bit0
                    for (k = 0; k < 8; k = k + 1) begin
                        bb[k] = uart_txd;
                        if (k < 7) repeat (CPB) @(posedge clk);
                    end
                    repeat (CPB + CPB/2) @(posedge clk);             // bit7 midpoint -> next frame start
                    resp[bytei] = bb;
                end
            end
        end
    endtask

    // check_values: 0 = only SOF/status checked (SOF-collision garbage inputs)
    task automatic run_packet(input [7:0] chk, input is_nack, input check_values);
        integer j;
        begin
            fork
                begin
                    uart_send(8'hAA);
                    for (j = 0; j < NFB; j = j + 1) uart_send(fb[j]);
                    uart_send(chk);
                end
                uart_recv8(recv_ok, RXWAIT);
            join
            if (!recv_ok) begin
                $display("  FAIL: no response (timeout waiting for SOF)"); fails = fails + 1;
            end else begin
                got_prob   = {resp[3], resp[2], resp[1]};
                got_spread = {resp[6], resp[5], resp[4]};
                $display("  resp SOF=%02x prob=%06x spread=%06x status=%02x (LED rx=%b err=%b res=%b)",
                         resp[0], got_prob, got_spread, resp[7], led[0], led[1], led[2]);
                if (resp[0] !== 8'h55) begin $display("  FAIL: SOF=0x%02x (exp 0x55)", resp[0]); fails = fails + 1; end
                if (is_nack) begin
                    if (resp[7] !== 8'h01) begin $display("  FAIL: NACK status=0x%02x (exp 0x01)", resp[7]); fails = fails + 1; end
                    else $display("  NACK ok (status 0x01)");
                end else begin
                    if (resp[7] !== 8'h00) begin $display("  FAIL: status=0x%02x (exp 0x00)", resp[7]); fails = fails + 1; end
                    if (check_values) begin
                        if (got_prob !== exp_prob) begin
                            $display("  FAIL: prob=%06x golden=%06x", got_prob, exp_prob); fails = fails + 1;
                        end
                        if (got_spread !== exp_spread) begin
                            $display("  FAIL: spread=%06x golden=%06x", got_spread, exp_spread); fails = fails + 1;
                        end
                        if (got_prob === exp_prob && got_spread === exp_spread)
                            $display("  bit-exact vs golden");
                    end
                end
            end
        end
    endtask

    initial begin
        $readmemh("tb_inputs.mem", feat_mem);
        $readmemh("golden_fixed.mem", golden_mem);

        repeat (8) @(posedge clk); rst = 0; repeat (8) @(posedge clk);
        fails = 0;

        for (g = 0; g < NG; g = g + 1) begin
            // serialize: feature i -> bytes 3i (LSB), 3i+1, 3i+2 (MSB)
            csum = 8'h00;
            for (i = 0; i < 21; i = i + 1) begin
                fb[3*i]   = feat_mem[g*21 + i][7:0];
                fb[3*i+1] = feat_mem[g*21 + i][15:8];
                fb[3*i+2] = feat_mem[g*21 + i][23:16];
            end
            for (i = 0; i < NFB; i = i + 1) csum = csum ^ fb[i];
            exp_prob   = golden_mem[g*2];
            exp_spread = golden_mem[g*2 + 1];
            $display("game %0d (good packet):", g);
            run_packet(csum, 1'b0, 1'b1);
            repeat (4*CPB) @(posedge clk);     // settle before next packet
        end

        $display("NACK test (corrupted checksum):");
        run_packet(csum ^ 8'hFF, 1'b1, 1'b0);

        // SOF-collision: every feature byte == 0xAA (the request SOF marker).
        // Framing is in RECV_FEATURES and must treat these as data. 63 bytes of
        // 0xAA XOR to 0xAA (odd count). Values not checked (garbage features).
        for (i = 0; i < NFB; i = i + 1) fb[i] = 8'hAA;
        csum = 8'hAA;
        $display("SOF-collision test (all feature bytes = 0xAA):");
        run_packet(csum, 1'b0, 1'b0);

        $display("\nFULL-UART TEST (GBDT): %0s (%0d failures)", (fails==0)?"PASS":"FAIL", fails);
        $finish;
    end
endmodule
