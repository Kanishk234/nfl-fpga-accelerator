// Phase 6 full-chain functional test — drives the ENTIRE `top` over real UART.
// Board scenario in simulation: serialize a packet into uart_rxd at 115200 baud, let
// UART RX -> framing -> controller -> real MLP IP -> framing TX run, then deserialize
// uart_txd and check the 4-byte response (good packets + a corrupted-checksum NACK).
//
// Concurrent send+receive: the DUT's turnaround is fast enough that it begins
// transmitting the response before the last sent byte's stop bit finishes, so the
// receiver must be armed (in parallel) BEFORE the response starts. The receiver locks
// to the SOF start edge once, then samples the 4 contiguous 10-bit frames at fixed
// offsets (robust against mostly-low bytes like 0x80 that defeat per-byte resync).
`timescale 1ns/1ps
module tb_top_uart;
    localparam integer CPB = 868;          // CLKS_PER_BIT = 100e6 / 115200
    localparam integer NG  = 3;            // games driven over UART
    localparam integer RXWAIT = 400000;    // recv armed before send; must outlast send+compute

    reg clk = 0, rst = 1;
    reg uart_rxd = 1;                       // idle high
    wire uart_txd;
    wire [2:0] led;
    always #5 clk = ~clk;                   // 100 MHz

    top u_top (.clk(clk), .rst(rst), .uart_rxd(uart_rxd), .uart_txd(uart_txd), .led(led));

    reg [7:0] feat_mem [0:50*21-1];
    reg [7:0] resp [0:3];
    reg [7:0] fb [0:20];
    reg [7:0] csum;
    integer g, i, fails;
    reg recv_ok;
    reg [8*256-1:0] in_path;

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

    // Receive the 4-byte response. Lock to the SOF start edge once, then sample all four
    // contiguous 10-bit frames at fixed offsets (1.5 CPB to a frame's bit0, +1 CPB/bit,
    // +1.5 CPB from bit7 to the next frame's start).
    task automatic uart_recv4(output [7:0] r0, output [7:0] r1, output [7:0] r2, output [7:0] r3,
                              output okf, input integer maxwait);
        integer bytei, k, guard;
        reg [7:0] bb;
        begin
            okf = 1'b1; r0=8'h00; r1=8'h00; r2=8'h00; r3=8'h00;
            guard = 0;                                               // ensure idle high
            while (uart_txd !== 1'b1 && guard < maxwait) begin @(posedge clk); guard = guard + 1; end
            guard = 0;                                               // wait for SOF start (low)
            while (uart_txd !== 1'b0 && guard < maxwait) begin @(posedge clk); guard = guard + 1; end
            if (guard >= maxwait) okf = 1'b0;
            else begin
                for (bytei = 0; bytei < 4; bytei = bytei + 1) begin
                    repeat (CPB + CPB/2) @(posedge clk);             // -> middle of this frame's bit0
                    for (k = 0; k < 8; k = k + 1) begin
                        bb[k] = uart_txd;
                        if (k < 7) repeat (CPB) @(posedge clk);
                    end
                    repeat (CPB + CPB/2) @(posedge clk);             // bit7 midpoint -> next frame start
                    case (bytei) 0: r0=bb; 1: r1=bb; 2: r2=bb; 3: r3=bb; endcase
                end
            end
        end
    endtask

    task automatic run_packet(input [7:0] chk, input is_nack);
        integer j;
        begin
            // Arm the receiver in parallel with the sender (DUT may respond before send ends).
            fork
                begin
                    uart_send(8'hAA);
                    for (j = 0; j < 21; j = j + 1) uart_send(fb[j]);
                    uart_send(chk);
                end
                uart_recv4(resp[0], resp[1], resp[2], resp[3], recv_ok, RXWAIT);
            join
            if (!recv_ok) begin
                $display("  FAIL: no response (timeout waiting for SOF)"); fails = fails + 1;
            end else begin
                $display("  resp = %02x %02x %02x %02x  (LED rx=%b err=%b res=%b)",
                         resp[0], resp[1], resp[2], resp[3], led[0], led[1], led[2]);
                if (resp[0] !== 8'h55) begin $display("  FAIL: SOF=0x%02x (exp 0x55)", resp[0]); fails = fails + 1; end
                if (is_nack) begin
                    if (resp[3] !== 8'h01) begin $display("  FAIL: NACK status=0x%02x (exp 0x01)", resp[3]); fails = fails + 1; end
                    else $display("  NACK ok (status 0x01)");
                end else begin
                    if (resp[3] !== 8'h00) begin $display("  FAIL: status=0x%02x (exp 0x00)", resp[3]); fails = fails + 1; end
                    $display("  win=%0d spread=%0d", resp[1], $signed(resp[2]));
                end
            end
        end
    endtask

    initial begin
        if (!$value$plusargs("IN=%s", in_path)) in_path = "tb_inputs.mem";
        $readmemh(in_path, feat_mem);

        repeat (8) @(posedge clk); rst = 0; repeat (8) @(posedge clk);
        fails = 0;

        for (g = 0; g < NG; g = g + 1) begin
            csum = 8'h00;
            for (i = 0; i < 21; i = i + 1) begin fb[i] = feat_mem[g*21 + i]; csum = csum ^ fb[i]; end
            $display("game %0d (good packet):", g);
            run_packet(csum, 1'b0);
            repeat (4*CPB) @(posedge clk);     // settle before next packet
        end

        csum = 8'h00;
        for (i = 0; i < 21; i = i + 1) begin fb[i] = feat_mem[i]; csum = csum ^ fb[i]; end
        $display("NACK test (corrupted checksum):");
        run_packet(csum ^ 8'hFF, 1'b1);

        // SOF-collision: every feature byte == 0xAA (the request SOF marker). Framing is in
        // RECV_FEATURES and must treat these as data, not a new SOF. Good checksum -> status 0x00.
        for (i = 0; i < 21; i = i + 1) fb[i] = 8'hAA;
        csum = 8'h00; for (i = 0; i < 21; i = i + 1) csum = csum ^ fb[i];   // = 0xAA (21 is odd)
        $display("SOF-collision test (all features = 0xAA):");
        run_packet(csum, 1'b0);

        $display("\nFULL-UART TEST: %0s (%0d failures)", (fails==0)?"PASS":"FAIL", fails);
        $finish;
    end
endmodule
