// Top-level wrapper — wires UART, framing, MLP controller, and hls4ml io_stream IP.
// Board: Basys 3 (Artix-7 XC7A35T)
// Clock: 100 MHz on-board oscillator (W5)
// UART: 115200 baud, 8N1 on USB-UART bridge (B18=RX, A18=TX per Digilent master XDC)
// Reset: BTNC center button, active high (U18)
//
// REWRITTEN 2026-06-17 for the io_stream IP (AXI4-Stream + ap_ctrl_hs). The previous
// full design wired the old ap_memory IP that deadlocked in hardware (AUDIT_REPORT.md
// §1). Re-extract the new Phase 4 IP (impl/ip export) over artifacts/ip_repo/ before
// synthesizing — the old ip_repo still holds the deadlocking io_serial IP.
`timescale 1ns / 1ps
module top (
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

    // hls4ml IP uses synchronous active-LOW reset.
    wire ap_rst_n = ~rst_sync;

    // --- uart_rx <-> uart_framing -----------------------------------------------
    wire [7:0] rx_data;
    wire       rx_done;

    // --- uart_framing <-> uart_tx -----------------------------------------------
    wire [7:0] tx_data;
    wire       tx_start;
    wire       tx_busy;

    // --- uart_framing <-> mlp_controller ----------------------------------------
    wire [167:0] feature_bus;
    wire         packet_valid;
    wire         packet_error;

    // --- ap_ctrl_hs: mlp_controller <-> myproject -------------------------------
    wire ap_start;
    wire ap_done;
    wire ap_idle;
    wire ap_ready;

    // --- AXI4-Stream: mlp_controller <-> myproject ------------------------------
    wire [671:0] features_TDATA;
    wire         features_TVALID;
    wire         features_TREADY;
    wire [31:0]  layer9_out_TDATA;
    wire         layer9_out_TVALID;
    wire         layer9_out_TREADY;
    wire [31:0]  layer10_out_TDATA;
    wire         layer10_out_TVALID;
    wire         layer10_out_TREADY;

    // --- mlp_controller -> uart_framing -----------------------------------------
    wire [7:0] result_win;
    wire [7:0] result_spread;
    wire       result_valid;
    wire       result_timeout;

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

    uart_framing u_frame (
        .clk           (clk),
        .rst           (rst_sync),
        .rx_data       (rx_data),
        .rx_done       (rx_done),
        .tx_data       (tx_data),
        .tx_start      (tx_start),
        .tx_busy       (tx_busy),
        .feature_bus   (feature_bus),
        .packet_valid  (packet_valid),
        .packet_error  (packet_error),
        .result_win    (result_win),
        .result_spread (result_spread),
        .result_valid  (result_valid),
        .result_timeout(result_timeout)
    );

    mlp_controller u_ctrl (
        .clk                (clk),
        .rst                (rst_sync),
        .feature_bus        (feature_bus),
        .packet_valid       (packet_valid),
        .ap_start           (ap_start),
        .ap_done            (ap_done),
        .ap_idle            (ap_idle),
        .ap_ready           (ap_ready),
        .features_TDATA     (features_TDATA),
        .features_TVALID    (features_TVALID),
        .features_TREADY    (features_TREADY),
        .layer9_out_TDATA   (layer9_out_TDATA),
        .layer9_out_TVALID  (layer9_out_TVALID),
        .layer9_out_TREADY  (layer9_out_TREADY),
        .layer10_out_TDATA  (layer10_out_TDATA),
        .layer10_out_TVALID (layer10_out_TVALID),
        .layer10_out_TREADY (layer10_out_TREADY),
        .result_win         (result_win),
        .result_spread      (result_spread),
        .result_valid       (result_valid),
        .result_timeout     (result_timeout)
    );

    // hls4ml io_stream MLP IP (Phase 4 export — xilinx_com_hls_myproject_1_0).
    // Port names verified against myproject_prj/solution1/syn/verilog/myproject.v.
    myproject u_mlp_ip (
        .ap_clk             (clk),
        .ap_rst_n           (ap_rst_n),
        .ap_start           (ap_start),
        .ap_done            (ap_done),
        .ap_idle            (ap_idle),
        .ap_ready           (ap_ready),
        .features_TDATA     (features_TDATA),
        .features_TVALID    (features_TVALID),
        .features_TREADY    (features_TREADY),
        .layer9_out_TDATA   (layer9_out_TDATA),
        .layer9_out_TVALID  (layer9_out_TVALID),
        .layer9_out_TREADY  (layer9_out_TREADY),
        .layer10_out_TDATA  (layer10_out_TDATA),
        .layer10_out_TVALID (layer10_out_TVALID),
        .layer10_out_TREADY (layer10_out_TREADY)
    );

    // --- Sticky debug LEDs — latch on first event, clear on reset (AUDIT §2) -----
    // LD2 is the direct probe for "MLP produced a result": with the old deadlocking
    // IP it never lit; with this io_stream IP it must light on a valid packet.
    reg led_rx, led_err, led_res;
    always @(posedge clk) begin
        if (rst_sync) begin
            led_rx  <= 1'b0;
            led_err <= 1'b0;
            led_res <= 1'b0;
        end else begin
            if (rx_done)       led_rx  <= 1'b1;  // LD0: any byte received
            if (packet_error)  led_err <= 1'b1;  // LD1: checksum NACK fired
            if (result_valid)  led_res <= 1'b1;  // LD2: MLP produced a result
        end
    end
    assign led = {led_res, led_err, led_rx};

endmodule
