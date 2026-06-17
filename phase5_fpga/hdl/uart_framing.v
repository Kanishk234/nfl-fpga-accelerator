// UART framing layer — packet assembly, SOF detection, checksum verification.
//
// RX packet: 0xAA | feature[0..20] (21 bytes) | XOR checksum
// TX packet: 0x55 | win_prob | spread | status
//
// feature_bus is a flattened 168-bit bus (Verilog-2001 has no array ports).
// Feature i occupies bits [i*8+7 : i*8].
//
// TX side handles both normal results (result_valid) and NACK on checksum error.
// Each SEND_ state asserts tx_start for one clock then waits for busy=0
// before sending the next byte.
`timescale 1ns / 1ps
module uart_framing (
    input             clk,
    input             rst,
    // from uart_rx
    input      [7:0]  rx_data,
    input             rx_done,
    // to uart_tx
    output reg [7:0]  tx_data,
    output reg        tx_start,
    input             tx_busy,
    // to mlp_controller — 21 features, flattened; feature[i] = feature_bus[i*8+7:i*8]
    output reg [167:0] feature_bus,
    output reg         packet_valid,
    output reg         packet_error,
    // from mlp_controller
    input      [7:0]  result_win,
    input      [7:0]  result_spread,
    input             result_valid,
    input             result_timeout   // MLP watchdog fired (AUDIT_REPORT.md §5.2)
);

    // -----------------------------------------------------------------------
    // RX FSM
    // -----------------------------------------------------------------------
    localparam RX_WAIT_SOF      = 2'd0;
    localparam RX_RECV_FEATURES = 2'd1;
    localparam RX_RECV_CHECKSUM = 2'd2;

    reg [1:0] rx_state;
    reg [4:0] byte_cnt;   // 0-20 (21 features)
    reg [7:0] checksum;   // running XOR of received feature bytes

    always @(posedge clk) begin
        packet_valid <= 1'b0;
        packet_error <= 1'b0;

        if (rst) begin
            rx_state  <= RX_WAIT_SOF;
            byte_cnt  <= 5'd0;
            checksum  <= 8'd0;
            feature_bus <= 168'd0;
        end else if (rx_done) begin
            case (rx_state)
                RX_WAIT_SOF: begin
                    if (rx_data == 8'hAA) begin
                        byte_cnt <= 5'd0;
                        checksum <= 8'd0;
                        rx_state <= RX_RECV_FEATURES;
                    end
                    // any other byte: stay in WAIT_SOF (resync)
                end

                RX_RECV_FEATURES: begin
                    feature_bus[byte_cnt*8 +: 8] <= rx_data;
                    checksum <= checksum ^ rx_data;
                    if (byte_cnt == 5'd20) begin
                        rx_state <= RX_RECV_CHECKSUM;
                    end else begin
                        byte_cnt <= byte_cnt + 1;
                    end
                end

                RX_RECV_CHECKSUM: begin
                    if (rx_data == checksum) begin
                        packet_valid <= 1'b1;
                    end else begin
                        packet_error <= 1'b1;
                    end
                    rx_state <= RX_WAIT_SOF;
                end

                default: rx_state <= RX_WAIT_SOF;
            endcase
        end
    end

    // -----------------------------------------------------------------------
    // TX FSM
    // -----------------------------------------------------------------------
    localparam TX_WAIT   = 3'd0;
    localparam TX_SOF    = 3'd1;
    localparam TX_WIN    = 3'd2;
    localparam TX_SPREAD = 3'd3;
    localparam TX_STATUS = 3'd4;

    reg [2:0] tx_state;
    reg [7:0] tx_win_lat, tx_spread_lat, tx_status_lat;

    always @(posedge clk) begin
        tx_start <= 1'b0;   // default: no start pulse

        if (rst) begin
            tx_state     <= TX_WAIT;
            tx_data      <= 8'd0;
            tx_win_lat   <= 8'd0;
            tx_spread_lat <= 8'd0;
            tx_status_lat <= 8'd0;
        end else begin
            case (tx_state)
                TX_WAIT: begin
                    if (result_valid) begin
                        tx_win_lat    <= result_win;
                        tx_spread_lat <= result_spread;
                        tx_status_lat <= 8'h00;
                        tx_state      <= TX_SOF;
                    end else if (packet_error) begin
                        // NACK: send error response immediately, no MLP result
                        tx_win_lat    <= 8'h00;
                        tx_spread_lat <= 8'h00;
                        tx_status_lat <= 8'h01;
                        tx_state      <= TX_SOF;
                    end else if (result_timeout) begin
                        // Inference watchdog fired: distinct status so the host knows the
                        // MLP never finished, rather than just timing out. (status 0x02)
                        tx_win_lat    <= 8'h00;
                        tx_spread_lat <= 8'h00;
                        tx_status_lat <= 8'h02;
                        tx_state      <= TX_SOF;
                    end
                end

                TX_SOF: begin
                    if (!tx_busy) begin
                        tx_data  <= 8'h55;
                        tx_start <= 1'b1;
                        tx_state <= TX_WIN;
                    end
                end

                TX_WIN: begin
                    // Guard !tx_start: uart_tx's busy register takes 1 clock to rise
                    // after start is seen; without this, TX_WIN fires while uart_tx
                    // is still processing the previous tx_start pulse.
                    if (!tx_busy && !tx_start) begin
                        tx_data  <= tx_win_lat;
                        tx_start <= 1'b1;
                        tx_state <= TX_SPREAD;
                    end
                end

                TX_SPREAD: begin
                    if (!tx_busy && !tx_start) begin
                        tx_data  <= tx_spread_lat;
                        tx_start <= 1'b1;
                        tx_state <= TX_STATUS;
                    end
                end

                TX_STATUS: begin
                    if (!tx_busy && !tx_start) begin
                        tx_data  <= tx_status_lat;
                        tx_start <= 1'b1;
                        tx_state <= TX_WAIT;
                    end
                end

                default: tx_state <= TX_WAIT;
            endcase
        end
    end
endmodule
