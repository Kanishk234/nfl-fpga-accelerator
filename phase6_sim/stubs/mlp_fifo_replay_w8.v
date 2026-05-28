// mlp_fifo_replay_w8.v
// Multi-replay FIFOs for hls4ml rf_gt_nin layers in iverilog simulation.
//
// hls4ml dense_resource (rf_gt_nin) layers iterate over all N_in inputs
// multiple times (once per output partition).  The synthesised FIFOs have
// depth=2 and are designed for the real streaming datapath where inputs
// arrive on-demand; in a sequential simulation the FIFO is pre-filled and
// must replay its contents N times before accepting new data.
//
// layer3_out: written once with 128 entries by Pipeline_VITIS_LOOP_46_1;
//   config4 (128→64, rf_gt_nin) reads 4×128 = 512 times → _replay4
//
// layer5_out: written once with 64 entries by Pipeline_VITIS_LOOP_46_11;
//   config6 (64→32, rf_gt_nin) reads 8×64 = 512 times → _replay8
`timescale 1ns/1ps

// --------------------------------------------------------------------------
// Replay-4 FIFO (replaces myproject_fifo_w8_d2_S for layer3_out_fifo_U)
// --------------------------------------------------------------------------
module myproject_fifo_w8_d2_S_replay4 (
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
    localparam DEPTH      = 256;
    localparam AW         = 8;
    localparam N_REPLAY   = 4;   // total passes through stored data

    reg [7:0]    mem [0:DEPTH-1];
    reg [AW:0]   count;
    reg [AW-1:0] wr_ptr, rd_ptr;
    reg [AW-1:0] n_written;
    reg [2:0]    passes_done;

    wire push = if_write_ce & if_write & if_full_n;
    wire pop  = if_read_ce  & if_read  & if_empty_n;

    assign if_full_n  = (count < DEPTH);
    assign if_empty_n = (count > 0);
    assign if_dout    = mem[rd_ptr];

    always @(posedge clk) begin
        if (reset) begin
            count       <= 0;
            wr_ptr      <= 0;
            rd_ptr      <= 0;
            n_written   <= 0;
            passes_done <= 0;
        end else if (push && !pop) begin
            if (count == 0 && passes_done == N_REPLAY-1) begin
                // All passes done — fresh inference
                mem[0]      <= if_din;
                wr_ptr      <= 1;
                rd_ptr      <= 0;
                count       <= 1;
                n_written   <= 1;
                passes_done <= 0;
            end else begin
                mem[wr_ptr] <= if_din;
                wr_ptr      <= wr_ptr + 1;
                n_written   <= n_written + 1;
                count       <= count + 1;
            end
        end else if (!push && pop) begin
            if (count == 1 && passes_done < N_REPLAY-1) begin
                // End of pass — replay from beginning
                rd_ptr      <= 0;
                count       <= n_written;
                passes_done <= passes_done + 1;
            end else begin
                rd_ptr <= rd_ptr + 1;
                count  <= count - 1;
            end
        end else if (push && pop) begin
            mem[wr_ptr] <= if_din;
            wr_ptr      <= wr_ptr + 1;
            n_written   <= n_written + 1;
            rd_ptr      <= rd_ptr + 1;
        end
    end
endmodule

// --------------------------------------------------------------------------
// Replay-8 FIFO (replaces myproject_fifo_w8_d2_S for layer5_out_fifo_U)
// --------------------------------------------------------------------------
module myproject_fifo_w8_d2_S_replay8 (
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
    localparam DEPTH      = 256;
    localparam AW         = 8;
    localparam N_REPLAY   = 8;   // total passes through stored data

    reg [7:0]    mem [0:DEPTH-1];
    reg [AW:0]   count;
    reg [AW-1:0] wr_ptr, rd_ptr;
    reg [AW-1:0] n_written;
    reg [3:0]    passes_done;

    wire push = if_write_ce & if_write & if_full_n;
    wire pop  = if_read_ce  & if_read  & if_empty_n;

    assign if_full_n  = (count < DEPTH);
    assign if_empty_n = (count > 0);
    assign if_dout    = mem[rd_ptr];

    always @(posedge clk) begin
        if (reset) begin
            count       <= 0;
            wr_ptr      <= 0;
            rd_ptr      <= 0;
            n_written   <= 0;
            passes_done <= 0;
        end else if (push && !pop) begin
            if (count == 0 && passes_done == N_REPLAY-1) begin
                // All passes done — fresh inference
                mem[0]      <= if_din;
                wr_ptr      <= 1;
                rd_ptr      <= 0;
                count       <= 1;
                n_written   <= 1;
                passes_done <= 0;
            end else begin
                mem[wr_ptr] <= if_din;
                wr_ptr      <= wr_ptr + 1;
                n_written   <= n_written + 1;
                count       <= count + 1;
            end
        end else if (!push && pop) begin
            if (count == 1 && passes_done < N_REPLAY-1) begin
                // End of pass — replay from beginning
                rd_ptr      <= 0;
                count       <= n_written;
                passes_done <= passes_done + 1;
            end else begin
                rd_ptr <= rd_ptr + 1;
                count  <= count - 1;
            end
        end else if (push && pop) begin
            mem[wr_ptr] <= if_din;
            wr_ptr      <= wr_ptr + 1;
            n_written   <= n_written + 1;
            rd_ptr      <= rd_ptr + 1;
        end
    end
endmodule
