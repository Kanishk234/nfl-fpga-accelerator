// Sigmoid ROM — 1024 x 12-bit, synchronous read (1-cycle latency).
//
// Contents come from phase6_sim_conifer/chain_golden/sigmoid_lut.mem, generated
// by make_chain_golden.py. The spec is locked there (do not regenerate one side
// without the other — the golden and this ROM must stay bit-identical):
//   ROM[i] = round(sigmoid(-8 + (i + 0.5)/64) * 4096), unsigned 12-bit
// The index is computed in gbdt_controller.v from the stage-1 margin:
//   clamp(margin, -32768, 32767) -> offset-binary -> top 10 bits.
`timescale 1ns / 1ps
module sigmoid_rom (
    input             clk,
    input      [9:0]  addr,
    output reg [11:0] data
);

    reg [11:0] rom [0:1023];

    initial $readmemh("sigmoid_lut.mem", rom);

    always @(posedge clk)
        data <= rom[addr];

endmodule
