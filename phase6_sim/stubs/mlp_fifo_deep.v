// mlp_fifo_deep.v
// Deep FIFO replacements for hls4ml streaming simulation.
// The synthesized FIFOs (myproject_fifo_w*_d2_S) have depth=2, which causes
// deadlocks in iverilog simulation because the producer fills the FIFO before
// the sequential FSM activates the consumer. Depth=256 prevents this.
// These modules have identical port signatures to the originals.
`timescale 1ns/1ps

// -----------------------------------------------------------------------
// 18-bit wide deep FIFO (replaces myproject_fifo_w18_d2_S)
// -----------------------------------------------------------------------
module myproject_fifo_w18_d2_S (
    input  wire        clk,
    input  wire        reset,
    output wire        if_full_n,
    input  wire        if_write_ce,
    input  wire        if_write,
    input  wire [17:0] if_din,
    output wire        if_empty_n,
    input  wire        if_read_ce,
    input  wire        if_read,
    output wire [17:0] if_dout
);
    localparam DEPTH = 256;
    localparam AW    = 8;

    reg [17:0] mem [0:DEPTH-1];
    reg [AW:0] count;
    reg [AW-1:0] wr_ptr, rd_ptr;

    wire push = if_write_ce & if_write & if_full_n;
    wire pop  = if_read_ce  & if_read  & if_empty_n;

    assign if_full_n  = (count < DEPTH);
    assign if_empty_n = (count > 0);
    assign if_dout    = mem[rd_ptr];

    always @(posedge clk) begin
        if (reset) begin
            count  <= 0;
            wr_ptr <= 0;
            rd_ptr <= 0;
        end else begin
            if (push) begin
                mem[wr_ptr] <= if_din;
                wr_ptr      <= wr_ptr + 1;
            end
            if (pop)
                rd_ptr <= rd_ptr + 1;
            if (push && !pop)
                count <= count + 1;
            else if (!push && pop)
                count <= count - 1;
        end
    end
endmodule

// -----------------------------------------------------------------------
// 8-bit wide deep FIFO (replaces myproject_fifo_w8_d2_S)
// -----------------------------------------------------------------------
module myproject_fifo_w8_d2_S (
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

    reg [7:0]  mem [0:DEPTH-1];
    reg [AW:0] count;
    reg [AW-1:0] wr_ptr, rd_ptr;

    wire push = if_write_ce & if_write & if_full_n;
    wire pop  = if_read_ce  & if_read  & if_empty_n;

    assign if_full_n  = (count < DEPTH);
    assign if_empty_n = (count > 0);
    assign if_dout    = mem[rd_ptr];

    always @(posedge clk) begin
        if (reset) begin
            count  <= 0;
            wr_ptr <= 0;
            rd_ptr <= 0;
        end else begin
            if (push) begin
                mem[wr_ptr] <= if_din;
                wr_ptr      <= wr_ptr + 1;
            end
            if (pop)
                rd_ptr <= rd_ptr + 1;
            if (push && !pop)
                count <= count + 1;
            else if (!push && pop)
                count <= count - 1;
        end
    end
endmodule

// -----------------------------------------------------------------------
// 32-bit wide deep FIFO (replaces myproject_fifo_w32_d2_S)
// -----------------------------------------------------------------------
module myproject_fifo_w32_d2_S (
    input  wire        clk,
    input  wire        reset,
    output wire        if_full_n,
    input  wire        if_write_ce,
    input  wire        if_write,
    input  wire [31:0] if_din,
    output wire        if_empty_n,
    input  wire        if_read_ce,
    input  wire        if_read,
    output wire [31:0] if_dout
);
    localparam DEPTH = 256;
    localparam AW    = 8;

    reg [31:0] mem [0:DEPTH-1];
    reg [AW:0] count;
    reg [AW-1:0] wr_ptr, rd_ptr;

    wire push = if_write_ce & if_write & if_full_n;
    wire pop  = if_read_ce  & if_read  & if_empty_n;

    assign if_full_n  = (count < DEPTH);
    assign if_empty_n = (count > 0);
    assign if_dout    = mem[rd_ptr];

    always @(posedge clk) begin
        if (reset) begin
            count  <= 0;
            wr_ptr <= 0;
            rd_ptr <= 0;
        end else begin
            if (push) begin
                mem[wr_ptr] <= if_din;
                wr_ptr      <= wr_ptr + 1;
            end
            if (pop)
                rd_ptr <= rd_ptr + 1;
            if (push && !pop)
                count <= count + 1;
            else if (!push && pop)
                count <= count - 1;
        end
    end
endmodule
