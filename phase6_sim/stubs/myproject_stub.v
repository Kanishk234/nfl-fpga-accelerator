// myproject_stub.v
// Behavioural stub for hls4ml MLP. Used in unit, integration, and regression sims.
// NOT added to Vivado project.
//
// Two injection paths for expected outputs:
//   1. DUT hierarchy (cocotb): dut.u_mlp.win_val.value = X
//      Used by integration and regression tests (in-process, set per game)
//   2. $value$plusargs: +WIN_OUT=<hex>+SPREAD_OUT=<hex>
//      Fallback for subprocess invocation
//
// Port widths confirmed from phase4_hls/...syn/verilog/myproject.v:
//   features_q0 [17:0], layer9_out [17:0], layer10_out [31:0]
// Encoding: win byte in layer9_out[11:4], spread byte in layer10_out[23:16]

module myproject #(
    parameter SIM_LATENCY = 20    // cycles to simulate before producing output
)(
    input  wire        ap_clk,
    input  wire        ap_rst,
    input  wire        ap_start,
    output reg         ap_done,
    output reg         ap_idle,
    output reg         ap_ready,
    // ap_memory feature interface
    output reg  [4:0]  features_address0,
    output reg         features_ce0,
    input  wire [17:0] features_q0,
    // outputs — widths match synthesis report
    output reg  [17:0] layer9_out,
    output reg         layer9_out_ap_vld,
    output reg  [31:0] layer10_out,
    output reg         layer10_out_ap_vld
);

    // Testbench-writable output registers
    // cocotb writes: dut.u_mlp.win_val.value = computed_win_18bit
    reg [17:0] win_val    = 18'h00C00;    // default ~75% — overwritten per game
    reg [31:0] spread_val = 32'hFFFB0000; // default -5 — overwritten per game

    integer latency_count;
    reg     running;

    initial begin
        ap_done  = 0; ap_idle  = 1; ap_ready = 1;
        layer9_out = 0; layer9_out_ap_vld = 0;
        layer10_out = 0; layer10_out_ap_vld = 0;
        features_address0 = 0; features_ce0 = 0;
        latency_count = 0; running = 0;
    end

    always @(posedge ap_clk) begin
        ap_done            <= 0;
        layer9_out_ap_vld  <= 0;
        layer10_out_ap_vld <= 0;
        features_ce0       <= 0;

        if (ap_rst) begin
            running       <= 0;
            latency_count <= 0;
            ap_idle       <= 1;
            ap_ready      <= 1;
        end else if (ap_start && !running) begin
            running       <= 1;
            latency_count <= 0;
            ap_idle       <= 0;
            ap_ready      <= 0;
        end else if (running) begin
            latency_count <= latency_count + 1;

            // Simulate sequential feature reads during inference
            if (latency_count < 21) begin
                features_ce0      <= 1;
                features_address0 <= latency_count[4:0];
            end

            if (latency_count == SIM_LATENCY - 1) begin
                // Re-read plusargs at output time — subprocess regression path
                // Hierarchy writes (cocotb path) are already in win_val/spread_val
                begin : read_plusargs
                    reg [31:0] tmp_win, tmp_spread;
                    if ($value$plusargs("WIN_OUT=%h",    tmp_win))    win_val    <= tmp_win[17:0];
                    if ($value$plusargs("SPREAD_OUT=%h", tmp_spread)) spread_val <= tmp_spread;
                end

                layer9_out          <= win_val;
                layer9_out_ap_vld   <= 1;
                layer10_out         <= spread_val;
                layer10_out_ap_vld  <= 1;
                ap_done             <= 1;
                ap_idle             <= 1;
                ap_ready            <= 1;
                running             <= 0;
                latency_count       <= 0;
            end
        end
    end
endmodule
