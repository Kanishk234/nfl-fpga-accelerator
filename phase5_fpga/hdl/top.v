// Top-level wrapper — wires UART, framing, MLP controller, and hls4ml IP.
// Board: Basys 3 (Artix-7 XC7A35T)
// Clock: 100 MHz on-board oscillator (W5)
// UART: 115200 baud, 8N1 on USB-UART bridge (B18=RX, A18=TX)
// Reset: BTNC center button, active high (U18)
`timescale 1ns / 1ps
module top (
    input  clk,       // 100 MHz
    input  rst,       // synchronous active-high, BTNC
    input  uart_rxd,  // from USB-UART chip
    output uart_txd   // to USB-UART chip
);

    // Internal wires between uart_rx and uart_framing
    wire [7:0] rx_data;
    wire       rx_done;

    // Internal wires between uart_framing and uart_tx
    wire [7:0] tx_data;
    wire       tx_start;
    wire       tx_busy;

    // Internal wires between uart_framing and mlp_controller
    wire [167:0] feature_bus;
    wire         packet_valid;
    wire         packet_error;

    // ap_ctrl_hs: mlp_controller → myproject
    wire ap_start;
    wire ap_done;
    wire ap_idle;
    wire ap_ready;

    // ap_memory: myproject drives address+CE, mlp_controller drives data
    wire [4:0]  features_address0;
    wire        features_ce0;
    wire [17:0] features_q0;

    // ap_vld outputs: myproject → mlp_controller
    wire [17:0] layer9_out;
    wire        layer9_out_ap_vld;
    wire [31:0] layer10_out;
    wire        layer10_out_ap_vld;

    // Result: mlp_controller → uart_framing
    wire [7:0] result_win;
    wire [7:0] result_spread;
    wire       result_valid;

    uart_rx #(
        .CLK_FREQ (100_000_000),
        .BAUD_RATE(115_200)
    ) u_rx (
        .clk (clk),
        .rst (rst),
        .rx  (uart_rxd),
        .data(rx_data),
        .done(rx_done)
    );

    uart_tx #(
        .CLK_FREQ (100_000_000),
        .BAUD_RATE(115_200)
    ) u_tx (
        .clk  (clk),
        .rst  (rst),
        .data (tx_data),
        .start(tx_start),
        .tx   (uart_txd),
        .busy (tx_busy)
    );

    uart_framing u_frame (
        .clk          (clk),
        .rst          (rst),
        .rx_data      (rx_data),
        .rx_done      (rx_done),
        .tx_data      (tx_data),
        .tx_start     (tx_start),
        .tx_busy      (tx_busy),
        .feature_bus  (feature_bus),
        .packet_valid (packet_valid),
        .packet_error (packet_error),
        .result_win   (result_win),
        .result_spread(result_spread),
        .result_valid (result_valid)
    );

    mlp_controller u_ctrl (
        .clk                (clk),
        .rst                (rst),
        .feature_bus        (feature_bus),
        .packet_valid       (packet_valid),
        .ap_start           (ap_start),
        .ap_done            (ap_done),
        .ap_idle            (ap_idle),
        .ap_ready           (ap_ready),
        .features_address0  (features_address0),
        .features_ce0       (features_ce0),
        .features_q0        (features_q0),
        .layer9_out         (layer9_out),
        .layer9_out_ap_vld  (layer9_out_ap_vld),
        .layer10_out        (layer10_out),
        .layer10_out_ap_vld (layer10_out_ap_vld),
        .result_win         (result_win),
        .result_spread      (result_spread),
        .result_valid       (result_valid)
    );

    // hls4ml MLP IP (instantiated from Phase 4 IP zip — xilinx_com_hls_myproject_1_0)
    myproject u_mlp_ip (
        .ap_clk             (clk),
        .ap_rst             (rst),
        .ap_start           (ap_start),
        .ap_done            (ap_done),
        .ap_idle            (ap_idle),
        .ap_ready           (ap_ready),
        .features_address0  (features_address0),
        .features_ce0       (features_ce0),
        .features_q0        (features_q0),
        .layer9_out         (layer9_out),
        .layer9_out_ap_vld  (layer9_out_ap_vld),
        .layer10_out        (layer10_out),
        .layer10_out_ap_vld (layer10_out_ap_vld)
    );

endmodule
