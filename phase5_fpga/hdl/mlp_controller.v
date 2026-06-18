// MLP controller — sequences the hls4ml IP (io_stream / AXI4-Stream interface).
//
// REWRITTEN 2026-06-17 for the io_stream IP (was ap_memory/io_serial — see
// AUDIT_REPORT.md §1 and phase4_hls/PHASE4_COMPLETE.md ADDENDUM). The old IP
// deadlocked in hardware; the io_stream IP uses a DATAFLOW region with AXI-Stream
// ports and an ap_ctrl_hs block-level handshake.
//
// Per-inference sequence:
//   1. Wait for ap_idle && packet_valid.
//   2. Pack 21 feature bytes into the 672-bit features_TDATA word (one beat,
//      21 lanes x 32 bits; each byte placed at lane[11:4]); assert ap_start.
//   3. Push the beat: hold features_TVALID until features_TREADY handshake.
//   4. Accept the two output beats: hold layer9/layer10 TREADY, capture TDATA
//      on each TVALID.
//   5. Encode win/spread bytes, pulse result_valid for one clock.
//
// features_TDATA lane encoding (byte -> ap_fixed<18,6> in low 18 bits of a 32-bit lane):
//   lane = {20'b0, byte, 4'b0}  -> value bits [11:4]=byte -> byte/256 ~= byte/255 (0.4% err)
//
// Output encoding (NOTE widths changed vs the old io_serial IP):
//   win    = layer9_out_TDATA[17:0]  as ap_fixed<18,6>  (was [11:4] of an 18-bit bus)
//   spread = layer10_out_TDATA[31:0] as ap_fixed<32,16> (integer byte = [23:16])
//
// rst is active-high (board BTNC). top.v derives the IP's active-low ap_rst_n.
`timescale 1ns / 1ps
module mlp_controller #(
    // WAIT watchdog: inference latency is ~1,760 cycles; 100,000 cycles = 1 ms @ 100 MHz
    // is generous. If the IP never finishes, return to IDLE and raise result_timeout so
    // the host gets a distinct status byte instead of a silent hang (AUDIT_REPORT.md §5.2).
    parameter [19:0] TIMEOUT_CYCLES = 20'd100_000
)(
    input              clk,
    input              rst,
    // from uart_framing
    input  [167:0]     feature_bus,
    input              packet_valid,
    // ap_ctrl_hs to/from hls4ml MLP IP
    output reg         ap_start,
    input              ap_done,    // intentionally unused: completion is gated on capturing both
                                   // output beats (win_captured && spread_captured), which is
                                   // strictly stronger. Vivado's "ap_done unconnected" info is expected.
    input              ap_idle,
    input              ap_ready,
    // AXI4-Stream input (features) -> MLP IP
    output reg [671:0] features_TDATA,
    output reg         features_TVALID,
    input              features_TREADY,
    // AXI4-Stream outputs from MLP IP
    input  [31:0]      layer9_out_TDATA,
    input              layer9_out_TVALID,
    output reg         layer9_out_TREADY,
    input  [31:0]      layer10_out_TDATA,
    input              layer10_out_TVALID,
    output reg         layer10_out_TREADY,
    // to uart_framing
    output reg [7:0]   result_win,
    output reg [7:0]   result_spread,
    output reg         result_valid,
    output reg         result_timeout
);

    localparam IDLE = 2'd0;
    localparam RUN  = 2'd1;
    localparam SEND = 2'd2;

    reg [1:0]  state;
    reg [31:0] win_word, spread_word;
    reg        win_captured, spread_captured;
    reg [19:0] timeout_cnt;

    integer i;

    always @(posedge clk) begin
        // One-clock pulses default low.
        result_valid   <= 1'b0;
        result_timeout <= 1'b0;

        if (rst) begin
            state            <= IDLE;
            ap_start         <= 1'b0;
            features_TVALID  <= 1'b0;
            features_TDATA   <= 672'd0;
            layer9_out_TREADY  <= 1'b0;
            layer10_out_TREADY <= 1'b0;
            win_word         <= 32'd0;
            spread_word      <= 32'd0;
            win_captured     <= 1'b0;
            spread_captured  <= 1'b0;
            result_win       <= 8'd0;
            result_spread    <= 8'd0;
            timeout_cnt      <= 20'd0;
        end else begin
            case (state)
                IDLE: begin
                    ap_start        <= 1'b0;
                    features_TVALID <= 1'b0;
                    win_captured    <= 1'b0;
                    spread_captured <= 1'b0;
                    timeout_cnt     <= 20'd0;
                    if (packet_valid && ap_idle) begin
                        // Pack 21 features into the 672-bit beat: lane i = {20'b0, byte, 4'b0}.
                        for (i = 0; i < 21; i = i + 1)
                            features_TDATA[i*32 +: 32] <= {20'b0, feature_bus[i*8 +: 8], 4'b0};
                        ap_start          <= 1'b1;  // held until ap_ready (ap_ctrl_hs)
                        features_TVALID   <= 1'b1;  // held until features_TREADY handshake
                        layer9_out_TREADY  <= 1'b1;
                        layer10_out_TREADY <= 1'b1;
                        state             <= RUN;
                    end
                end

                RUN: begin
                    timeout_cnt <= timeout_cnt + 1'b1;

                    // ap_ctrl_hs: drop ap_start once the IP has accepted the start.
                    if (ap_ready)
                        ap_start <= 1'b0;

                    // Input handshake: one beat carries all 21 features.
                    if (features_TVALID && features_TREADY)
                        features_TVALID <= 1'b0;

                    // Output handshakes: capture each result beat, then stop accepting.
                    if (layer9_out_TVALID && layer9_out_TREADY) begin
                        win_word          <= layer9_out_TDATA;
                        win_captured      <= 1'b1;
                        layer9_out_TREADY <= 1'b0;
                    end
                    if (layer10_out_TVALID && layer10_out_TREADY) begin
                        spread_word        <= layer10_out_TDATA;
                        spread_captured    <= 1'b1;
                        layer10_out_TREADY <= 1'b0;
                    end

                    if (win_captured && spread_captured) begin
                        state <= SEND;
                    end else if (timeout_cnt >= TIMEOUT_CYCLES) begin
                        // IP never produced both results — recover instead of hanging.
                        result_timeout     <= 1'b1;
                        ap_start           <= 1'b0;
                        features_TVALID    <= 1'b0;
                        layer9_out_TREADY  <= 1'b0;
                        layer10_out_TREADY <= 1'b0;
                        state              <= IDLE;
                    end
                end

                SEND: begin
                    // Win saturation (AUDIT_REPORT.md §5.3): sigmoid is in [0,1], but clamp
                    // so a negative value reads 0x00 and >= 1.0 reads 0xFF instead of wrapping.
                    // win_word[17:0] is ap_fixed<18,6>: [17]=sign, [16:12]=integer, [11:0]=frac.
                    result_win    <= win_word[17]        ? 8'h00 :   // negative -> 0
                                     (|win_word[16:12])  ? 8'hFF :   // >= 1.0  -> 255
                                                           win_word[11:4];
                    // Spread: integer byte of ap_fixed<32,16>. Floors (e.g. -3.2 -> -4) while
                    // the Python golden values round; absorbed by the +/-3 tolerance. Do not
                    // "fix" the off-by-one without re-checking the golden vectors.
                    result_spread <= spread_word[23:16];
                    result_valid  <= 1'b1;
                    state         <= IDLE;
                end

                default: state <= IDLE;
            endcase
        end
    end
endmodule
