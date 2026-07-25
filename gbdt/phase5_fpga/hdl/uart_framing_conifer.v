// UART framing layer (conifer/GBDT variant) — packet assembly, SOF detection,
// checksum verification. Adapted from phase5_fpga/hdl/uart_framing.v.
//
// The GBDT track uses RAW features (trees are scale-invariant — no MinMax/INT8),
// so one byte per feature cannot work (elo ~1500). Each feature is sent as its
// full ap_fixed<24,12> representation, and results return full-width so the
// board output is bit-exact comparable to chain_golden/.
//
// RX packet (65 bytes): 0xAA | 63 feature bytes | XOR checksum
//   feature i = bytes 3i (LSB), 3i+1, 3i+2 (MSB) — 24-bit two's complement,
//   value = round(raw_feature * 4096) (ap_fixed<24,12>)
// TX packet (8 bytes):  0x55 | win_prob[7:0] [15:8] [23:16]
//                            | spread[7:0] [15:8] [23:16] | status
//   status: 0x00 ok, 0x01 checksum NACK, 0x02 inference watchdog timeout
//
// feature_bus is a flattened 504-bit bus; feature i = feature_bus[i*24+23 : i*24].
`timescale 1ns / 1ps
module uart_framing_conifer #(
    // Inter-byte RX watchdog (AUDIT_REPORT.md §5.1) — same rationale as the MLP
    // framing: resync to WAIT_SOF if a byte drops mid-packet. ~10 ms @ 100 MHz;
    // must exceed the worst legitimate inter-byte gap (~8,680 cycles @ 115200).
    parameter [19:0] RX_TIMEOUT_CYCLES = 20'd1_000_000
)(
    input              clk,
    input              rst,
    // from uart_rx
    input      [7:0]   rx_data,
    input              rx_done,
    // to uart_tx
    output reg [7:0]   tx_data,
    output reg         tx_start,
    input              tx_busy,
    // to gbdt_controller
    output reg [503:0] feature_bus,
    output reg         packet_valid,
    output reg         packet_error,
    // from gbdt_controller
    input      [23:0]  result_win_prob,
    input      [23:0]  result_spread,
    input              result_valid,
    input              result_timeout
);

    localparam N_FEATURE_BYTES = 63;   // 21 features x 3 bytes

    // -----------------------------------------------------------------------
    // RX FSM
    // -----------------------------------------------------------------------
    localparam RX_WAIT_SOF      = 2'd0;
    localparam RX_RECV_FEATURES = 2'd1;
    localparam RX_RECV_CHECKSUM = 2'd2;

    reg [1:0]  rx_state;
    reg [5:0]  byte_cnt;        // 0-62
    reg [7:0]  checksum;        // running XOR of received feature bytes
    reg [19:0] rx_timeout_cnt;

    always @(posedge clk) begin
        packet_valid <= 1'b0;
        packet_error <= 1'b0;

        if (rst) begin
            rx_state       <= RX_WAIT_SOF;
            byte_cnt       <= 6'd0;
            checksum       <= 8'd0;
            feature_bus    <= 504'd0;
            rx_timeout_cnt <= 20'd0;
        end else begin
            if (rx_done || rx_state == RX_WAIT_SOF)
                rx_timeout_cnt <= 20'd0;
            else
                rx_timeout_cnt <= rx_timeout_cnt + 1'b1;

            if (rx_done) begin
                case (rx_state)
                    RX_WAIT_SOF: begin
                        if (rx_data == 8'hAA) begin
                            byte_cnt <= 6'd0;
                            checksum <= 8'd0;
                            rx_state <= RX_RECV_FEATURES;
                        end
                        // any other byte: stay in WAIT_SOF (resync)
                    end

                    RX_RECV_FEATURES: begin
                        feature_bus[byte_cnt*8 +: 8] <= rx_data;
                        checksum <= checksum ^ rx_data;
                        if (byte_cnt == N_FEATURE_BYTES - 1) begin
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
            end else if (rx_state != RX_WAIT_SOF && rx_timeout_cnt >= RX_TIMEOUT_CYCLES - 1) begin
                rx_state <= RX_WAIT_SOF;
                byte_cnt <= 6'd0;
                checksum <= 8'd0;
            end
        end
    end

    // -----------------------------------------------------------------------
    // TX FSM — 8 bytes: SOF, 3x win_prob, 3x spread, status
    // -----------------------------------------------------------------------
    localparam TX_WAIT = 1'b0;
    localparam TX_SEND = 1'b1;

    reg        tx_state;
    reg [2:0]  tx_idx;
    reg [23:0] tx_win_lat, tx_spread_lat;
    reg [7:0]  tx_status_lat;

    reg [7:0] tx_byte;
    always @(*) begin
        case (tx_idx)
            3'd0:    tx_byte = 8'h55;
            3'd1:    tx_byte = tx_win_lat[7:0];
            3'd2:    tx_byte = tx_win_lat[15:8];
            3'd3:    tx_byte = tx_win_lat[23:16];
            3'd4:    tx_byte = tx_spread_lat[7:0];
            3'd5:    tx_byte = tx_spread_lat[15:8];
            3'd6:    tx_byte = tx_spread_lat[23:16];
            default: tx_byte = tx_status_lat;
        endcase
    end

    always @(posedge clk) begin
        tx_start <= 1'b0;   // default: no start pulse

        if (rst) begin
            tx_state      <= TX_WAIT;
            tx_data       <= 8'd0;
            tx_idx        <= 3'd0;
            tx_win_lat    <= 24'd0;
            tx_spread_lat <= 24'd0;
            tx_status_lat <= 8'd0;
        end else begin
            case (tx_state)
                TX_WAIT: begin
                    if (result_valid) begin
                        tx_win_lat    <= result_win_prob;
                        tx_spread_lat <= result_spread;
                        tx_status_lat <= 8'h00;
                        tx_idx        <= 3'd0;
                        tx_state      <= TX_SEND;
                    end else if (packet_error) begin
                        // NACK: send error response immediately, no result
                        tx_win_lat    <= 24'd0;
                        tx_spread_lat <= 24'd0;
                        tx_status_lat <= 8'h01;
                        tx_idx        <= 3'd0;
                        tx_state      <= TX_SEND;
                    end else if (result_timeout) begin
                        // Inference watchdog fired (status 0x02)
                        tx_win_lat    <= 24'd0;
                        tx_spread_lat <= 24'd0;
                        tx_status_lat <= 8'h02;
                        tx_idx        <= 3'd0;
                        tx_state      <= TX_SEND;
                    end
                end

                TX_SEND: begin
                    // Guard !tx_start: uart_tx's busy register takes 1 clock to rise
                    // after start is seen (same guard as the MLP framing).
                    if (!tx_busy && !tx_start) begin
                        tx_data  <= tx_byte;
                        tx_start <= 1'b1;
                        if (tx_idx == 3'd7)
                            tx_state <= TX_WAIT;
                        else
                            tx_idx <= tx_idx + 1;
                    end
                end
            endcase
        end
    end
endmodule
