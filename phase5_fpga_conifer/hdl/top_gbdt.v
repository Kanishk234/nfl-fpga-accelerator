// Top-level wrapper (conifer/GBDT variant) — wires UART, framing, controller,
// sigmoid ROM, and BOTH conifer IPs (stacked two-stage pipeline).
// Board: Basys 3 (Artix-7 XC7A35T) — same pins/constraints as the MLP top
// (phase5_fpga/constraints/basys3.xdc is reused unmodified).
// Clock: 100 MHz (W5). UART: 115200 8N1 (B18=RX, A18=TX). Reset: BTNC (U18).
//
// Differences vs phase5_fpga/hdl/top.v (MLP):
//   - Two IPs, chained by gbdt_controller: conifer_win -> sigmoid ROM ->
//     conifer_spread -> +vegas adder. No AXIS — parallel 24-bit feature ports.
//   - conifer IPs use active-HIGH ap_rst (hls4ml's was active-low ap_rst_n).
//   - Feature/result buses are 24-bit ap_fixed<24,12> (raw features, no scaler).
// uart_rx.v / uart_tx.v are sourced unchanged from phase5_fpga/hdl/.
`timescale 1ns / 1ps
module top_gbdt (
    input        clk,       // 100 MHz
    input        rst,       // active-high, BTNC (U18)
    input        uart_rxd,  // from USB-UART chip (B18)
    output       uart_txd,  // to USB-UART chip (A18)
    output [2:0] led        // sticky debug: LD0=rx, LD1=err, LD2=result
);

    // --- Reset synchronizer (2-FF) — double-flop the async BTNC press. -----------
    reg rst_meta, rst_sync;
    always @(posedge clk) begin
        rst_meta <= rst;
        rst_sync <= rst_meta;
    end

    // --- uart_rx <-> uart_framing_conifer ----------------------------------------
    wire [7:0] rx_data;
    wire       rx_done;

    // --- uart_framing_conifer <-> uart_tx ----------------------------------------
    wire [7:0] tx_data;
    wire       tx_start;
    wire       tx_busy;

    // --- uart_framing_conifer <-> gbdt_controller --------------------------------
    wire [503:0] feature_bus;
    wire         packet_valid;
    wire         packet_error;
    wire [23:0]  result_win_prob;
    wire [23:0]  result_spread;
    wire         result_valid;
    wire         result_timeout;

    // --- gbdt_controller <-> conifer IPs ------------------------------------------
    wire [503:0] x_bus;
    wire [23:0]  win_prob;
    wire         win_ap_start, win_ap_done, win_ap_idle, win_ap_ready;
    wire [23:0]  win_score;
    wire         win_score_vld;
    wire         spread_ap_start, spread_ap_done, spread_ap_idle, spread_ap_ready;
    wire [23:0]  spread_score;
    wire         spread_score_vld;

    // --- gbdt_controller <-> sigmoid ROM ------------------------------------------
    wire [9:0]  sig_addr;
    wire [11:0] sig_data;

    uart_rx #(
        .CLK_FREQ (100_000_000),
        .BAUD_RATE(115_200)
    ) u_rx (
        .clk (clk),
        .rst (rst_sync),
        .rx  (uart_rxd),
        .data(rx_data),
        .done(rx_done)
    );

    uart_tx #(
        .CLK_FREQ (100_000_000),
        .BAUD_RATE(115_200)
    ) u_tx (
        .clk  (clk),
        .rst  (rst_sync),
        .data (tx_data),
        .start(tx_start),
        .tx   (uart_txd),
        .busy (tx_busy)
    );

    uart_framing_conifer u_frame (
        .clk            (clk),
        .rst            (rst_sync),
        .rx_data        (rx_data),
        .rx_done        (rx_done),
        .tx_data        (tx_data),
        .tx_start       (tx_start),
        .tx_busy        (tx_busy),
        .feature_bus    (feature_bus),
        .packet_valid   (packet_valid),
        .packet_error   (packet_error),
        .result_win_prob(result_win_prob),
        .result_spread  (result_spread),
        .result_valid   (result_valid),
        .result_timeout (result_timeout)
    );

    gbdt_controller u_ctrl (
        .clk             (clk),
        .rst             (rst_sync),
        .feature_bus     (feature_bus),
        .packet_valid    (packet_valid),
        .x_bus           (x_bus),
        .win_prob        (win_prob),
        .win_ap_start    (win_ap_start),
        .win_ap_done     (win_ap_done),
        .win_ap_idle     (win_ap_idle),
        .win_ap_ready    (win_ap_ready),
        .win_score       (win_score),
        .win_score_vld   (win_score_vld),
        .spread_ap_start (spread_ap_start),
        .spread_ap_done  (spread_ap_done),
        .spread_ap_idle  (spread_ap_idle),
        .spread_ap_ready (spread_ap_ready),
        .spread_score    (spread_score),
        .spread_score_vld(spread_score_vld),
        .sig_addr        (sig_addr),
        .sig_data        (sig_data),
        .result_win_prob (result_win_prob),
        .result_spread   (result_spread),
        .result_valid    (result_valid),
        .result_timeout  (result_timeout)
    );

    sigmoid_rom u_sigmoid (
        .clk (clk),
        .addr(sig_addr),
        .data(sig_data)
    );

    // conifer stage 1 — win margin (Phase 4 export, syn/verilog netlist).
    // score_1 is a dead port of the conifer 1.9 template (input, never read
    // internally in either IP) — tied to 0.
    conifer_win u_win_ip (
        .ap_clk        (clk),
        .ap_rst        (rst_sync),          // active HIGH (conifer, not hls4ml)
        .ap_start      (win_ap_start),
        .ap_done       (win_ap_done),
        .ap_idle       (win_ap_idle),
        .ap_ready      (win_ap_ready),
        .x_0           (x_bus[0*24 +: 24]),
        .x_1           (x_bus[1*24 +: 24]),
        .x_2           (x_bus[2*24 +: 24]),
        .x_3           (x_bus[3*24 +: 24]),
        .x_4           (x_bus[4*24 +: 24]),
        .x_5           (x_bus[5*24 +: 24]),
        .x_6           (x_bus[6*24 +: 24]),
        .x_7           (x_bus[7*24 +: 24]),
        .x_8           (x_bus[8*24 +: 24]),
        .x_9           (x_bus[9*24 +: 24]),
        .x_10          (x_bus[10*24 +: 24]),
        .x_11          (x_bus[11*24 +: 24]),
        .x_12          (x_bus[12*24 +: 24]),
        .x_13          (x_bus[13*24 +: 24]),
        .x_14          (x_bus[14*24 +: 24]),
        .x_15          (x_bus[15*24 +: 24]),
        .x_16          (x_bus[16*24 +: 24]),
        .x_17          (x_bus[17*24 +: 24]),
        .x_18          (x_bus[18*24 +: 24]),
        .x_19          (x_bus[19*24 +: 24]),
        .x_20          (x_bus[20*24 +: 24]),
        .score_0       (win_score),
        .score_0_ap_vld(win_score_vld),
        .score_1       (24'd0)
    );

    // conifer stage 2 — spread residual; x_21 is the stacked win_prob.
    conifer_spread u_spread_ip (
        .ap_clk        (clk),
        .ap_rst        (rst_sync),
        .ap_start      (spread_ap_start),
        .ap_done       (spread_ap_done),
        .ap_idle       (spread_ap_idle),
        .ap_ready      (spread_ap_ready),
        .x_0           (x_bus[0*24 +: 24]),
        .x_1           (x_bus[1*24 +: 24]),
        .x_2           (x_bus[2*24 +: 24]),
        .x_3           (x_bus[3*24 +: 24]),
        .x_4           (x_bus[4*24 +: 24]),
        .x_5           (x_bus[5*24 +: 24]),
        .x_6           (x_bus[6*24 +: 24]),
        .x_7           (x_bus[7*24 +: 24]),
        .x_8           (x_bus[8*24 +: 24]),
        .x_9           (x_bus[9*24 +: 24]),
        .x_10          (x_bus[10*24 +: 24]),
        .x_11          (x_bus[11*24 +: 24]),
        .x_12          (x_bus[12*24 +: 24]),
        .x_13          (x_bus[13*24 +: 24]),
        .x_14          (x_bus[14*24 +: 24]),
        .x_15          (x_bus[15*24 +: 24]),
        .x_16          (x_bus[16*24 +: 24]),
        .x_17          (x_bus[17*24 +: 24]),
        .x_18          (x_bus[18*24 +: 24]),
        .x_19          (x_bus[19*24 +: 24]),
        .x_20          (x_bus[20*24 +: 24]),
        .x_21          (win_prob),
        .score_0       (spread_score),
        .score_0_ap_vld(spread_score_vld),
        .score_1       (24'd0)
    );

    // --- Sticky debug LEDs — latch on first event, clear on reset (AUDIT §2) -----
    reg led_rx, led_err, led_res;
    always @(posedge clk) begin
        if (rst_sync) begin
            led_rx  <= 1'b0;
            led_err <= 1'b0;
            led_res <= 1'b0;
        end else begin
            if (rx_done)      led_rx  <= 1'b1;  // LD0: any byte received
            if (packet_error) led_err <= 1'b1;  // LD1: checksum NACK fired
            if (result_valid) led_res <= 1'b1;  // LD2: pipeline produced a result
        end
    end
    assign led = {led_res, led_err, led_rx};

endmodule
