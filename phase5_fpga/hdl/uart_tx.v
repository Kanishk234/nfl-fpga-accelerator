// UART transmitter — 8N1, synchronous active-high reset
// tx idles high. Assert start=1 for one clock to send the byte in data.
// Caller must wait for busy=0 before asserting start again.
`timescale 1ns / 1ps
module uart_tx #(
    parameter CLK_FREQ  = 100_000_000,
    parameter BAUD_RATE = 115_200
) (
    input            clk,
    input            rst,
    input      [7:0] data,
    input            start,
    output reg       tx,
    output reg       busy
);
    localparam CLKS_PER_BIT = CLK_FREQ / BAUD_RATE;  // 868

    localparam IDLE  = 2'd0;
    localparam START = 2'd1;
    localparam DATA  = 2'd2;
    localparam STOP  = 2'd3;

    reg [1:0] state;
    reg [9:0] cnt;       // counts up to CLKS_PER_BIT (868 fits in 10 bits)
    reg [2:0] bit_idx;
    reg [7:0] shift_reg;

    always @(posedge clk) begin
        if (rst) begin
            state    <= IDLE;
            tx       <= 1'b1;    // idle high even during reset
            busy     <= 1'b0;
            cnt      <= 10'd0;
            bit_idx  <= 3'd0;
            shift_reg <= 8'd0;
        end else begin
            case (state)
                IDLE: begin
                    tx   <= 1'b1;
                    busy <= 1'b0;
                    if (start) begin
                        shift_reg <= data;
                        cnt       <= CLKS_PER_BIT - 1;
                        busy      <= 1'b1;
                        state     <= START;
                    end
                end

                START: begin
                    tx <= 1'b0;               // start bit
                    if (cnt == 0) begin
                        cnt     <= CLKS_PER_BIT - 1;
                        bit_idx <= 3'd0;
                        state   <= DATA;
                    end else begin
                        cnt <= cnt - 1;
                    end
                end

                DATA: begin
                    tx <= shift_reg[0];       // LSB first
                    if (cnt == 0) begin
                        shift_reg <= {1'b0, shift_reg[7:1]};
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
                    tx <= 1'b1;               // stop bit
                    if (cnt == 0) begin
                        busy  <= 1'b0;
                        state <= IDLE;
                    end else begin
                        cnt <= cnt - 1;
                    end
                end

                default: begin
                    tx    <= 1'b1;
                    state <= IDLE;
                end
            endcase
        end
    end
endmodule
