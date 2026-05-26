// UART receiver — 8N1, synchronous active-high reset
// CLK_FREQ / BAUD_RATE must equal 868 for 115200 baud at 100 MHz.
// rx is double-registered to prevent metastability from the async USB-UART chip.
`timescale 1ns / 1ps
module uart_rx #(
    parameter CLK_FREQ  = 100_000_000,
    parameter BAUD_RATE = 115_200
) (
    input            clk,
    input            rst,
    input            rx,
    output reg [7:0] data,
    output reg       done
);
    localparam CLKS_PER_BIT = CLK_FREQ / BAUD_RATE;  // 868
    localparam HALF_BIT     = CLKS_PER_BIT / 2;       // 434 — center of start bit

    localparam IDLE  = 2'd0;
    localparam START = 2'd1;
    localparam DATA  = 2'd2;
    localparam STOP  = 2'd3;

    reg        rx_meta, rx_sync;
    reg [1:0]  state;
    reg [9:0]  cnt;       // counts up to CLKS_PER_BIT (868 fits in 10 bits)
    reg [2:0]  bit_idx;
    reg [7:0]  shift_reg;

    // Double-register: prevent metastability from async RX line
    always @(posedge clk) begin
        rx_meta <= rx;
        rx_sync <= rx_meta;
    end

    always @(posedge clk) begin
        done <= 1'b0;
        if (rst) begin
            state    <= IDLE;
            cnt      <= 10'd0;
            bit_idx  <= 3'd0;
            shift_reg <= 8'd0;
            data     <= 8'd0;
        end else begin
            case (state)
                IDLE: begin
                    if (!rx_sync) begin           // falling edge = start bit
                        cnt   <= HALF_BIT - 1;
                        state <= START;
                    end
                end

                START: begin
                    if (cnt == 0) begin
                        if (!rx_sync) begin       // still low at mid-bit: valid start
                            cnt     <= CLKS_PER_BIT - 1;
                            bit_idx <= 3'd0;
                            state   <= DATA;
                        end else begin            // went high: false start, re-arm
                            state <= IDLE;
                        end
                    end else begin
                        cnt <= cnt - 1;
                    end
                end

                DATA: begin
                    if (cnt == 0) begin
                        shift_reg <= {rx_sync, shift_reg[7:1]};  // LSB first
                        cnt       <= CLKS_PER_BIT - 1;
                        if (bit_idx == 3'd7) begin
                            state <= STOP;
                        end else begin
                            bit_idx <= bit_idx + 1;
                        end
                    end else begin
                        cnt <= cnt - 1;
                    end
                end

                STOP: begin
                    if (cnt == 0) begin
                        data  <= shift_reg;
                        done  <= 1'b1;
                        state <= IDLE;
                    end else begin
                        cnt <= cnt - 1;
                    end
                end

                default: state <= IDLE;
            endcase
        end
    end
endmodule
