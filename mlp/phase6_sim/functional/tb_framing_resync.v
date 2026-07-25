// G6 test — uart_framing RX inter-byte watchdog (AUDIT §5.1).
// Drives rx_data/rx_done directly. Sends a PARTIAL packet (SOF + 5 features) then stalls past
// RX_TIMEOUT_CYCLES (dropped byte); the watchdog must resync to WAIT_SOF so a subsequent FULL
// packet is accepted. Without the watchdog the stale FSM would eat the next 0xAA as feature data
// and the packet would fail its checksum (packet_error, no packet_valid).
`timescale 1ns/1ps
module tb_framing_resync;
    reg clk = 0, rst = 1;
    reg [7:0] rx_data; reg rx_done;
    reg tx_busy = 0;
    reg [7:0] result_win = 0, result_spread = 0;
    reg result_valid = 0, result_timeout = 0;
    wire [7:0] tx_data; wire tx_start;
    wire [167:0] feature_bus; wire packet_valid, packet_error;
    integer i, pv = 0, pe = 0; reg [7:0] cs;

    always #5 clk = ~clk;

    uart_framing #(.RX_TIMEOUT_CYCLES(20'd200)) dut (
        .clk(clk), .rst(rst), .rx_data(rx_data), .rx_done(rx_done),
        .tx_data(tx_data), .tx_start(tx_start), .tx_busy(tx_busy),
        .feature_bus(feature_bus), .packet_valid(packet_valid), .packet_error(packet_error),
        .result_win(result_win), .result_spread(result_spread),
        .result_valid(result_valid), .result_timeout(result_timeout));

    always @(posedge clk) begin
        if (packet_valid) pv = pv + 1;
        if (packet_error) pe = pe + 1;
    end

    // Drive stimulus on negedge so the DUT samples a clean one-cycle rx_done pulse at posedge
    // (driving at posedge races the DUT's sample and yields a double pulse). The real uart_rx
    // already emits a one-cycle rx_done pulse.
    task automatic send_byte(input [7:0] b);
        begin
            @(negedge clk); rx_data = b; rx_done = 1'b1;
            @(negedge clk); rx_done = 1'b0;
            repeat (10) @(negedge clk);     // normal inter-byte gap (< RX_TIMEOUT_CYCLES)
        end
    endtask

    initial begin
        rx_data = 0; rx_done = 0;
        repeat (4) @(posedge clk); rst = 0; repeat (4) @(posedge clk);

        // 1) partial packet then DROP: SOF + 5 features, then stall > timeout
        send_byte(8'hAA);
        for (i = 0; i < 5; i = i + 1) send_byte(8'h11);
        repeat (300) @(posedge clk);        // > RX_TIMEOUT_CYCLES(200): watchdog resyncs

        // 2) a full valid packet must now be accepted (proves resync)
        cs = 8'h00;
        send_byte(8'hAA);
        for (i = 0; i < 21; i = i + 1) begin send_byte(8'h40); cs = cs ^ 8'h40; end
        send_byte(cs);
        repeat (20) @(posedge clk);

        if (pv == 1 && pe == 0)
            $display("RESYNC TEST: PASS (full packet accepted after mid-packet drop; pv=%0d pe=%0d)", pv, pe);
        else
            $display("RESYNC TEST: FAIL (pv=%0d pe=%0d)", pv, pe);
        $finish;
    end
endmodule
