// Deliberately-stalling io_stream stub of myproject — for exercising error paths that the
// real (working) IP never triggers:
//   * consumes the features beat (asserts features_TREADY) but NEVER asserts the output
//     TVALIDs -> the mlp_controller's WAIT watchdog must fire (result_timeout / status 0x02).
//   * optionally forces extreme layer9 values to exercise the controller's win saturation
//     (drive via +SAT=hi / +SAT=neg plusargs; default: never output -> timeout).
// iverilog/elaboration + small-sim only; NOT in the Vivado project.
`timescale 1ns/1ps
module myproject (
    input          ap_clk,
    input          ap_rst_n,
    input          ap_start,
    output reg     ap_done,
    output reg     ap_idle,
    output reg     ap_ready,
    input  [671:0] features_TDATA,
    input          features_TVALID,
    output reg     features_TREADY,
    output reg [31:0] layer9_out_TDATA,
    output reg     layer9_out_TVALID,
    input          layer9_out_TREADY,
    output reg [31:0] layer10_out_TDATA,
    output reg     layer10_out_TVALID,
    input          layer10_out_TREADY
);
    // SAT mode: "" -> stall forever (timeout); "hi" -> win>=1.0; "neg" -> win<0; "mid" -> normal.
    reg [8*8-1:0] sat;
    initial if (!$value$plusargs("SAT=%s", sat)) sat = "";

    localparam S_IDLE=2'd0, S_IN=2'd1, S_OUT=2'd2, S_DONE=2'd3;
    reg [1:0] st;
    reg got9, got10;
    reg emit;   // 1 = emit outputs (saturation modes), 0 = stall (timeout mode)

    always @(posedge ap_clk) begin
        if (!ap_rst_n) begin
            st<=S_IDLE; ap_done<=0; ap_idle<=1; ap_ready<=0; features_TREADY<=0;
            layer9_out_TVALID<=0; layer10_out_TVALID<=0; layer9_out_TDATA<=0; layer10_out_TDATA<=0;
            got9<=0; got10<=0;
            emit <= (sat=="hi") || (sat=="neg") || (sat=="mid");
        end else begin
            ap_done<=0; ap_ready<=0;
            case (st)
                S_IDLE: begin
                    ap_idle<=1;
                    if (ap_start) begin ap_idle<=0; ap_ready<=1; features_TREADY<=1; st<=S_IN; end
                end
                S_IN: begin
                    if (features_TVALID && features_TREADY) begin
                        features_TREADY<=0;
                        if (!emit) st<=S_IN;            // STALL: never produce outputs -> watchdog fires
                        else begin
                            // win: hi=1.0 (int bit set), neg=sign bit set, mid=0.5 (byte 0x80)
                            layer9_out_TDATA  <= (sat=="hi")  ? 32'h0001_0000 :
                                                 (sat=="neg") ? 32'h0002_0000 : 32'h0000_0800;
                            layer10_out_TDATA <= 32'h0003_0000;   // spread +3
                            layer9_out_TVALID<=1; layer10_out_TVALID<=1; got9<=0; got10<=0; st<=S_OUT;
                        end
                    end
                end
                S_OUT: begin
                    if (layer9_out_TVALID && layer9_out_TREADY) begin layer9_out_TVALID<=0; got9<=1; end
                    if (layer10_out_TVALID && layer10_out_TREADY) begin layer10_out_TVALID<=0; got10<=1; end
                    if ((got9||(layer9_out_TVALID&&layer9_out_TREADY)) &&
                        (got10||(layer10_out_TVALID&&layer10_out_TREADY))) st<=S_DONE;
                end
                S_DONE: begin ap_done<=1; ap_idle<=1; st<=S_IDLE; end
                default: st<=S_IDLE;
            endcase
        end
    end
endmodule
