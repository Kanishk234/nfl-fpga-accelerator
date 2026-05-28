// mlp_fifo_dual_w8.v
// Replay-once FIFO for layer7_out in the hls4ml-generated myproject.
//
// layer7_out is shared between two output heads (config8 in state14 for win
// probability, config10 in state18 for spread).  The HLS FSM runs them
// sequentially, but both read the same 32-entry FIFO.  A plain FIFO is
// drained by config8 and empty when config10 starts, causing a permanent stall.
//
// This module stores all written entries and, after the first full drain
// (config8), resets the read pointer so config10 can read the same data again.
// On the next inference the write pointer is reset when a new write arrives
// after both drains have completed.
`timescale 1ns/1ps

module myproject_fifo_w8_d2_S_dual (
    input  wire       clk,
    input  wire       reset,
    output wire       if_full_n,
    input  wire       if_write_ce,
    input  wire       if_write,
    input  wire [7:0] if_din,
    output wire       if_empty_n,
    input  wire       if_read_ce,
    input  wire       if_read,
    output wire [7:0] if_dout
);
    localparam DEPTH = 256;
    localparam AW    = 8;

    reg [7:0]    mem [0:DEPTH-1];
    reg [AW:0]   count;
    reg [AW-1:0] wr_ptr, rd_ptr;
    reg [AW-1:0] n_written;
    reg          replayed;

    wire push = if_write_ce & if_write & if_full_n;
    wire pop  = if_read_ce  & if_read  & if_empty_n;

    assign if_full_n  = (count < DEPTH);
    assign if_empty_n = (count > 0);
    assign if_dout    = mem[rd_ptr];

    always @(posedge clk) begin
        if (reset) begin
            count    <= 0;
            wr_ptr   <= 0;
            rd_ptr   <= 0;
            n_written <= 0;
            replayed <= 0;
        end else if (push && !pop) begin
            if (count == 0 && replayed) begin
                // Both consumers finished — start fresh for next inference
                mem[0]   <= if_din;
                wr_ptr   <= 1;
                rd_ptr   <= 0;
                count    <= 1;
                n_written <= 1;
                replayed <= 0;
            end else begin
                mem[wr_ptr] <= if_din;
                wr_ptr    <= wr_ptr + 1;
                n_written <= n_written + 1;
                count     <= count + 1;
            end
        end else if (!push && pop) begin
            if (count == 1 && !replayed) begin
                // First consumer (config8) drained — replay for second (config10)
                rd_ptr   <= 0;
                count    <= n_written;
                replayed <= 1;
            end else begin
                rd_ptr <= rd_ptr + 1;
                count  <= count - 1;
            end
        end else if (push && pop) begin
            // Simultaneous (should not occur in practice — different FSM states)
            mem[wr_ptr] <= if_din;
            wr_ptr    <= wr_ptr + 1;
            n_written <= n_written + 1;
            rd_ptr    <= rd_ptr + 1;
            // count unchanged
        end
    end
endmodule
