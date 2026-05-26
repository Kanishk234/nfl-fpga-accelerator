// MLP controller — sequences the ap_ctrl_hs handshake with the hls4ml IP
// and serves features via the ap_memory interface.
//
// ap_ctrl_hs protocol:
//   1. Wait for ap_idle=1
//   2. Assert ap_start=1 for exactly ONE clock (combinational from START_MLP state)
//   3. During inference: MLP drives features_address0+features_ce0; we respond with features_q0
//   4. MLP asserts layer9_out_ap_vld / layer10_out_ap_vld when outputs are ready
//   5. Assert result_valid for one clock with encoded win/spread bytes
//
// features_q0 encoding: 8-bit received byte → ap_fixed<18,6>
//   {6'b0, byte, 4'b0} places byte in fractional bits [11:4] → byte/256 ≈ byte/255 (0.4% err)
//
// Output encoding:
//   result_win    = layer9_out[11:4]   (top 8 fractional bits of fixed<18,6> ≈ prob×256)
//   result_spread = layer10_out[23:16] (integer byte of fixed<32,16> = spread in points)
//
// feature_bus is a flattened 168-bit bus; feature[i] = feature_bus[i*8+7:i*8].
`timescale 1ns / 1ps
module mlp_controller (
    input             clk,
    input             rst,
    // from uart_framing
    input  [167:0]    feature_bus,
    input             packet_valid,
    // ap_ctrl_hs to/from hls4ml MLP IP
    output            ap_start,
    input             ap_done,
    input             ap_idle,
    input             ap_ready,
    // ap_memory to/from hls4ml MLP IP (MLP drives address+CE, we drive data)
    input  [4:0]      features_address0,
    input             features_ce0,
    output reg [17:0] features_q0,
    // MLP outputs with ap_vld handshake
    input  [17:0]     layer9_out,
    input             layer9_out_ap_vld,
    input  [31:0]     layer10_out,
    input             layer10_out_ap_vld,
    // to uart_framing
    output reg [7:0]  result_win,
    output reg [7:0]  result_spread,
    output reg        result_valid
);

    localparam IDLE       = 2'd0;
    localparam START_MLP  = 2'd1;
    localparam WAIT_DONE  = 2'd2;
    localparam SEND_RESULT = 2'd3;

    reg [1:0]  state;
    reg [7:0]  feature_store [0:20];  // latched 8-bit features for MLP to read
    reg [17:0] win_result;
    reg [31:0] spread_result;
    reg        win_valid, spread_valid;

    integer i;

    // ap_start is combinational from state — exactly one clock wide (START_MLP state)
    assign ap_start = (state == START_MLP);

    // Synchronous feature read: MLP drives ce0+address0, we respond next cycle.
    // {6'b0, byte, 4'b0} = byte placed in fractional bits [11:4] of ap_fixed<18,6>.
    always @(posedge clk) begin
        if (features_ce0)
            features_q0 <= {6'b0, feature_store[features_address0], 4'b0};
    end

    always @(posedge clk) begin
        result_valid <= 1'b0;

        if (rst) begin
            state        <= IDLE;
            win_valid    <= 1'b0;
            spread_valid <= 1'b0;
            win_result   <= 18'd0;
            spread_result <= 32'd0;
            result_win   <= 8'd0;
            result_spread <= 8'd0;
            features_q0  <= 18'd0;
            for (i = 0; i <= 20; i = i + 1)
                feature_store[i] <= 8'd0;
        end else begin
            // Latch ap_vld outputs whenever they arrive — independently of state
            if (layer9_out_ap_vld) begin
                win_result <= layer9_out;
                win_valid  <= 1'b1;
            end
            if (layer10_out_ap_vld) begin
                spread_result <= layer10_out;
                spread_valid  <= 1'b1;
            end

            case (state)
                IDLE: begin
                    win_valid    <= 1'b0;
                    spread_valid <= 1'b0;
                    if (packet_valid && ap_idle) begin
                        // Latch all 21 features from the flattened bus
                        for (i = 0; i <= 20; i = i + 1)
                            feature_store[i] <= feature_bus[i*8 +: 8];
                        state <= START_MLP;
                    end
                end

                START_MLP: begin
                    // ap_start is high this cycle (combinational). Transition immediately
                    // so ap_start is low next cycle — exactly one-clock-wide pulse.
                    state <= WAIT_DONE;
                end

                WAIT_DONE: begin
                    // Stay until both output valid flags have been received
                    if (win_valid && spread_valid) begin
                        state <= SEND_RESULT;
                    end
                end

                SEND_RESULT: begin
                    result_win    <= win_result[11:4];    // top 8 frac bits of fixed<18,6>
                    result_spread <= spread_result[23:16]; // integer byte of fixed<32,16>
                    result_valid  <= 1'b1;
                    win_valid     <= 1'b0;
                    spread_valid  <= 1'b0;
                    state         <= IDLE;
                end

                default: state <= IDLE;
            endcase
        end
    end
endmodule
