// End-to-end timeout test: full `top` linked with the STALLING stub. Sends one good-checksum
// packet; the stub never completes, so the controller watchdog fires and framing must send
// response status 0x02 (55 00 00 02). Proves result_timeout -> framing -> uart_tx wiring. [G2]
// Compile with myproject_stub_stall.v as the `myproject`. top's default TIMEOUT_CYCLES=100000.
`timescale 1ns/1ps
module tb_top_timeout;
    localparam integer CPB = 868;
    localparam integer RXWAIT = 400000;
    reg clk = 0, rst = 1;
    reg uart_rxd = 1;
    wire uart_txd; wire [2:0] led;
    reg [7:0] resp [0:3]; reg [7:0] fb [0:20]; reg [7:0] csum; reg okf;
    integer i;
    always #5 clk = ~clk;

    top u_top (.clk(clk), .rst(rst), .uart_rxd(uart_rxd), .uart_txd(uart_txd), .led(led));

    task automatic uart_send(input [7:0] b);
        integer k;
        begin
            uart_rxd=1'b0; repeat(CPB) @(posedge clk);
            for (k=0;k<8;k=k+1) begin uart_rxd=b[k]; repeat(CPB) @(posedge clk); end
            uart_rxd=1'b1; repeat(CPB) @(posedge clk);
        end
    endtask

    task automatic uart_recv4(output [7:0] r0, output [7:0] r1, output [7:0] r2, output [7:0] r3,
                              output o, input integer maxwait);
        integer bi, k, guard; reg [7:0] bb;
        begin
            o=1; r0=0;r1=0;r2=0;r3=0;
            guard=0; while (uart_txd!==1'b1 && guard<maxwait) begin @(posedge clk); guard=guard+1; end
            guard=0; while (uart_txd!==1'b0 && guard<maxwait) begin @(posedge clk); guard=guard+1; end
            if (guard>=maxwait) o=0;
            else for (bi=0;bi<4;bi=bi+1) begin
                repeat(CPB+CPB/2) @(posedge clk);
                for (k=0;k<8;k=k+1) begin bb[k]=uart_txd; if(k<7) repeat(CPB) @(posedge clk); end
                repeat(CPB+CPB/2) @(posedge clk);
                case(bi) 0:r0=bb;1:r1=bb;2:r2=bb;3:r3=bb; endcase
            end
        end
    endtask

    initial begin
        for (i=0;i<21;i=i+1) fb[i]=8'h40;
        csum=8'h00; for (i=0;i<21;i=i+1) csum=csum^fb[i];
        repeat(8) @(posedge clk); rst=0; repeat(8) @(posedge clk);
        fork
            begin uart_send(8'hAA); for (i=0;i<21;i=i+1) uart_send(fb[i]); uart_send(csum); end
            uart_recv4(resp[0],resp[1],resp[2],resp[3], okf, RXWAIT);
        join
        if (!okf) $display("FAIL: no response (watchdog never produced a byte)");
        else begin
            $display("resp = %02x %02x %02x %02x (LED rx=%b err=%b res=%b)",
                     resp[0],resp[1],resp[2],resp[3], led[0],led[1],led[2]);
            if (resp[0]===8'h55 && resp[3]===8'h02) $display("TIMEOUT-OVER-UART: PASS (status 0x02)");
            else $display("FAIL: expected 55 xx xx 02, got %02x .. %02x", resp[0], resp[3]);
        end
        $finish;
    end
endmodule
