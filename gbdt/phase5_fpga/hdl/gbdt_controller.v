// GBDT controller — sequences the two conifer IPs through the stacked pipeline:
//
//   21 raw features -> conifer_win -> margin
//                   -> sigmoid ROM -> win_prob            (the stacked meta-feature)
//   [features, win_prob] -> conifer_spread -> residual
//   spread = residual + vegas_spread (feature 16)         (one adder)
//
// All values are ap_fixed<24,12> (24-bit two's complement, 12 fractional bits).
// The chain is modeled bit-exactly by phase6_sim_conifer/make_chain_golden.py —
// any change to the sigmoid indexing or the adder must be mirrored there.
//
// Both conifer IPs are ap_ctrl_hs with parallel 24-bit feature inputs and a
// score_0/score_0_ap_vld output (9-cycle latency, no streams — much simpler
// than the MLP's AXIS interface). Inputs must be held stable while the IP
// runs; they come from registers latched at packet time, so they are.
//
// Sigmoid index math (must match make_chain_golden.py hw_sigmoid()):
//   m       = margin as integer (already in fixed-point units)
//   clamp   to [-32768, 32767]           (margin in [-8.0, +8.0))
//   index   = (m + 32768) >> 6           = offset-binary top 10 bits
//   win_prob= {12'b0, ROM[index]}        (unsigned, always in [0,1))
//
// rst is active-high (board BTNC); the conifer IPs use active-HIGH ap_rst
// (unlike the hls4ml MLP IP's ap_rst_n).
`timescale 1ns / 1ps
module gbdt_controller #(
    // Watchdog for the whole 2-stage sequence. Real latency is ~22 cycles
    // (9 + 9 per IP + sigmoid + handshakes); 100,000 cycles = 1 ms @ 100 MHz
    // is generous. Mirrors mlp_controller's AUDIT_REPORT.md §5.2 watchdog.
    parameter [19:0] TIMEOUT_CYCLES = 20'd100_000
)(
    input               clk,
    input               rst,
    // from uart_framing_conifer — 21 features x 24 bits, feature[i] = [i*24+23 : i*24]
    input      [503:0]  feature_bus,
    input               packet_valid,
    // to both conifer IPs — latched features (top.v slices per-feature ports)
    output reg [503:0]  x_bus,
    output reg [23:0]   win_prob,       // spread IP's x_21 (stacked meta-feature)
    // ap_ctrl_hs to/from conifer_win
    output reg          win_ap_start,
    input               win_ap_done,    // unused: completion gated on score_0_ap_vld
    input               win_ap_idle,
    input               win_ap_ready,
    input      [23:0]   win_score,
    input               win_score_vld,
    // ap_ctrl_hs to/from conifer_spread
    output reg          spread_ap_start,
    input               spread_ap_done, // unused: same reason
    input               spread_ap_idle,
    input               spread_ap_ready,
    input      [23:0]   spread_score,
    input               spread_score_vld,
    // sigmoid ROM (sigmoid_rom.v, 1-cycle synchronous read)
    output     [9:0]    sig_addr,
    input      [11:0]   sig_data,
    // to uart_framing_conifer — full-width results for bit-exact golden checks
    output reg [23:0]   result_win_prob,
    output reg [23:0]   result_spread,
    output reg          result_valid,
    output reg          result_timeout
);

    localparam IDLE       = 3'd0;
    localparam WIN_RUN    = 3'd1;
    localparam SIG_READ   = 3'd2;  // ROM registers the address this cycle
    localparam SIG_LATCH  = 3'd3;  // ROM data valid; latch win_prob, start spread
    localparam SPREAD_RUN = 3'd4;
    localparam SEND       = 3'd5;

    reg [2:0]         state;
    reg signed [23:0] margin;
    reg [19:0]        timeout_cnt;

    // --- Sigmoid index: clamp margin to [-32768, 32767], then offset-binary ----
    // top 10 bits. -32768 -> idx 0, 0 -> idx 512, +32767 -> idx 1023 (matches
    // Python's (m + 32768) >> 6 exactly).
    wire signed [15:0] m_clamped =
        (margin < -24'sd32768) ? 16'sh8000 :
        (margin >  24'sd32767) ? 16'sh7FFF :
                                 margin[15:0];
    assign sig_addr = {~m_clamped[15], m_clamped[14:6]};

    // vegas_spread is feature 16 (artifacts/features.json) — the residual's
    // "+ vegas_spread" is one adder tapping an existing latched input.
    wire [23:0] vegas = x_bus[16*24 +: 24];

    always @(posedge clk) begin
        result_valid   <= 1'b0;
        result_timeout <= 1'b0;

        if (rst) begin
            state           <= IDLE;
            x_bus           <= 504'd0;
            win_prob        <= 24'd0;
            win_ap_start    <= 1'b0;
            spread_ap_start <= 1'b0;
            margin          <= 24'sd0;
            result_win_prob <= 24'd0;
            result_spread   <= 24'd0;
            timeout_cnt     <= 20'd0;
        end else begin
            case (state)
                IDLE: begin
                    win_ap_start    <= 1'b0;
                    spread_ap_start <= 1'b0;
                    timeout_cnt     <= 20'd0;
                    if (packet_valid && win_ap_idle && spread_ap_idle) begin
                        x_bus        <= feature_bus;
                        win_ap_start <= 1'b1;   // held until win_ap_ready (ap_ctrl_hs)
                        state        <= WIN_RUN;
                    end
                end

                WIN_RUN: begin
                    timeout_cnt <= timeout_cnt + 1'b1;
                    if (win_ap_ready)
                        win_ap_start <= 1'b0;
                    if (win_score_vld) begin
                        margin <= win_score;
                        state  <= SIG_READ;
                    end else if (timeout_cnt >= TIMEOUT_CYCLES) begin
                        result_timeout <= 1'b1;
                        win_ap_start   <= 1'b0;
                        state          <= IDLE;
                    end
                end

                SIG_READ: begin
                    // sig_addr is combinational from margin; the ROM registers it
                    // on this edge, so sig_data is valid in SIG_LATCH.
                    timeout_cnt <= timeout_cnt + 1'b1;
                    state       <= SIG_LATCH;
                end

                SIG_LATCH: begin
                    timeout_cnt     <= timeout_cnt + 1'b1;
                    win_prob        <= {12'd0, sig_data};  // spread IP's x_21
                    spread_ap_start <= 1'b1;               // x_21 stable from this edge on
                    state           <= SPREAD_RUN;
                end

                SPREAD_RUN: begin
                    timeout_cnt <= timeout_cnt + 1'b1;
                    if (spread_ap_ready)
                        spread_ap_start <= 1'b0;
                    if (spread_score_vld) begin
                        result_win_prob <= win_prob;
                        result_spread   <= spread_score + vegas;  // + vegas_spread
                        state           <= SEND;
                    end else if (timeout_cnt >= TIMEOUT_CYCLES) begin
                        result_timeout  <= 1'b1;
                        spread_ap_start <= 1'b0;
                        state           <= IDLE;
                    end
                end

                SEND: begin
                    result_valid <= 1'b1;
                    state        <= IDLE;
                end

                default: state <= IDLE;
            endcase
        end
    end
endmodule
