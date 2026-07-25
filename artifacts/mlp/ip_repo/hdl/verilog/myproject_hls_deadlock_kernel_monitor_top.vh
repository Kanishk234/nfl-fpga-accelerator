
wire kernel_monitor_reset;
wire kernel_monitor_clock;
wire kernel_monitor_report;
assign kernel_monitor_reset = ~ap_rst_n;
assign kernel_monitor_clock = ap_clk;
assign kernel_monitor_report = 1'b0;
wire [2:0] axis_block_sigs;
wire [13:0] inst_idle_sigs;
wire [9:0] inst_block_sigs;
wire kernel_block;

assign axis_block_sigs[0] = ~dense_array_ap_fixed_21u_array_ap_fixed_18_6_5_3_0_128u_config2_U0.features_TDATA_blk_n;
assign axis_block_sigs[1] = ~sigmoid_array_array_ap_fixed_18_6_5_3_0_1u_sigmoid_config9_U0.layer9_out_TDATA_blk_n;
assign axis_block_sigs[2] = ~dense_array_ap_fixed_32u_array_ap_fixed_32_16_5_3_0_1u_config10_U0.layer10_out_TDATA_blk_n;

assign inst_idle_sigs[0] = dense_array_ap_fixed_21u_array_ap_fixed_18_6_5_3_0_128u_config2_U0.ap_idle;
assign inst_block_sigs[0] = (dense_array_ap_fixed_21u_array_ap_fixed_18_6_5_3_0_128u_config2_U0.ap_done & ~dense_array_ap_fixed_21u_array_ap_fixed_18_6_5_3_0_128u_config2_U0.ap_continue) | ~dense_array_ap_fixed_21u_array_ap_fixed_18_6_5_3_0_128u_config2_U0.layer2_out_blk_n;
assign inst_idle_sigs[1] = relu_array_ap_fixed_128u_array_ap_fixed_8_4_5_3_0_128u_relu_config3_U0.ap_idle;
assign inst_block_sigs[1] = (relu_array_ap_fixed_128u_array_ap_fixed_8_4_5_3_0_128u_relu_config3_U0.ap_done & ~relu_array_ap_fixed_128u_array_ap_fixed_8_4_5_3_0_128u_relu_config3_U0.ap_continue) | ~relu_array_ap_fixed_128u_array_ap_fixed_8_4_5_3_0_128u_relu_config3_U0.layer2_out_blk_n | ~relu_array_ap_fixed_128u_array_ap_fixed_8_4_5_3_0_128u_relu_config3_U0.layer3_out_blk_n;
assign inst_idle_sigs[2] = dense_array_ap_fixed_128u_array_ap_fixed_18_6_5_3_0_64u_config4_U0.ap_idle;
assign inst_block_sigs[2] = (dense_array_ap_fixed_128u_array_ap_fixed_18_6_5_3_0_64u_config4_U0.ap_done & ~dense_array_ap_fixed_128u_array_ap_fixed_18_6_5_3_0_64u_config4_U0.ap_continue) | ~dense_array_ap_fixed_128u_array_ap_fixed_18_6_5_3_0_64u_config4_U0.layer3_out_blk_n | ~dense_array_ap_fixed_128u_array_ap_fixed_18_6_5_3_0_64u_config4_U0.layer4_out_blk_n;
assign inst_idle_sigs[3] = relu_array_ap_fixed_64u_array_ap_fixed_8_4_5_3_0_64u_relu_config5_U0.ap_idle;
assign inst_block_sigs[3] = (relu_array_ap_fixed_64u_array_ap_fixed_8_4_5_3_0_64u_relu_config5_U0.ap_done & ~relu_array_ap_fixed_64u_array_ap_fixed_8_4_5_3_0_64u_relu_config5_U0.ap_continue) | ~relu_array_ap_fixed_64u_array_ap_fixed_8_4_5_3_0_64u_relu_config5_U0.layer4_out_blk_n | ~relu_array_ap_fixed_64u_array_ap_fixed_8_4_5_3_0_64u_relu_config5_U0.layer5_out_blk_n;
assign inst_idle_sigs[4] = dense_array_ap_fixed_64u_array_ap_fixed_18_6_5_3_0_32u_config6_U0.ap_idle;
assign inst_block_sigs[4] = (dense_array_ap_fixed_64u_array_ap_fixed_18_6_5_3_0_32u_config6_U0.ap_done & ~dense_array_ap_fixed_64u_array_ap_fixed_18_6_5_3_0_32u_config6_U0.ap_continue) | ~dense_array_ap_fixed_64u_array_ap_fixed_18_6_5_3_0_32u_config6_U0.layer5_out_blk_n | ~dense_array_ap_fixed_64u_array_ap_fixed_18_6_5_3_0_32u_config6_U0.layer6_out_blk_n;
assign inst_idle_sigs[5] = relu_array_ap_fixed_32u_array_ap_fixed_8_4_5_3_0_32u_relu_config7_U0.ap_idle;
assign inst_block_sigs[5] = (relu_array_ap_fixed_32u_array_ap_fixed_8_4_5_3_0_32u_relu_config7_U0.ap_done & ~relu_array_ap_fixed_32u_array_ap_fixed_8_4_5_3_0_32u_relu_config7_U0.ap_continue) | ~relu_array_ap_fixed_32u_array_ap_fixed_8_4_5_3_0_32u_relu_config7_U0.layer6_out_blk_n | ~relu_array_ap_fixed_32u_array_ap_fixed_8_4_5_3_0_32u_relu_config7_U0.layer7_out_blk_n;
assign inst_idle_sigs[6] = clone_stream_array_ap_fixed_32u_array_ap_fixed_8_4_5_3_0_32u_32_U0.ap_idle;
assign inst_block_sigs[6] = (clone_stream_array_ap_fixed_32u_array_ap_fixed_8_4_5_3_0_32u_32_U0.ap_done & ~clone_stream_array_ap_fixed_32u_array_ap_fixed_8_4_5_3_0_32u_32_U0.ap_continue) | ~clone_stream_array_ap_fixed_32u_array_ap_fixed_8_4_5_3_0_32u_32_U0.layer7_out_blk_n | ~clone_stream_array_ap_fixed_32u_array_ap_fixed_8_4_5_3_0_32u_32_U0.layer11_cpy1_blk_n | ~clone_stream_array_ap_fixed_32u_array_ap_fixed_8_4_5_3_0_32u_32_U0.layer11_cpy2_blk_n;
assign inst_idle_sigs[7] = dense_array_ap_fixed_32u_array_ap_fixed_32_16_5_3_0_1u_config8_U0.ap_idle;
assign inst_block_sigs[7] = (dense_array_ap_fixed_32u_array_ap_fixed_32_16_5_3_0_1u_config8_U0.ap_done & ~dense_array_ap_fixed_32u_array_ap_fixed_32_16_5_3_0_1u_config8_U0.ap_continue) | ~dense_array_ap_fixed_32u_array_ap_fixed_32_16_5_3_0_1u_config8_U0.layer11_cpy1_blk_n | ~dense_array_ap_fixed_32u_array_ap_fixed_32_16_5_3_0_1u_config8_U0.layer8_out_blk_n;
assign inst_idle_sigs[8] = sigmoid_array_array_ap_fixed_18_6_5_3_0_1u_sigmoid_config9_U0.ap_idle;
assign inst_block_sigs[8] = (sigmoid_array_array_ap_fixed_18_6_5_3_0_1u_sigmoid_config9_U0.ap_done & ~sigmoid_array_array_ap_fixed_18_6_5_3_0_1u_sigmoid_config9_U0.ap_continue) | ~sigmoid_array_array_ap_fixed_18_6_5_3_0_1u_sigmoid_config9_U0.layer8_out_blk_n;
assign inst_idle_sigs[9] = dense_array_ap_fixed_32u_array_ap_fixed_32_16_5_3_0_1u_config10_U0.ap_idle;
assign inst_block_sigs[9] = (dense_array_ap_fixed_32u_array_ap_fixed_32_16_5_3_0_1u_config10_U0.ap_done & ~dense_array_ap_fixed_32u_array_ap_fixed_32_16_5_3_0_1u_config10_U0.ap_continue) | ~dense_array_ap_fixed_32u_array_ap_fixed_32_16_5_3_0_1u_config10_U0.layer11_cpy2_blk_n;

assign inst_idle_sigs[10] = 1'b0;
assign inst_idle_sigs[11] = dense_array_ap_fixed_21u_array_ap_fixed_18_6_5_3_0_128u_config2_U0.ap_idle;
assign inst_idle_sigs[12] = sigmoid_array_array_ap_fixed_18_6_5_3_0_1u_sigmoid_config9_U0.ap_idle;
assign inst_idle_sigs[13] = dense_array_ap_fixed_32u_array_ap_fixed_32_16_5_3_0_1u_config10_U0.ap_idle;

myproject_hls_deadlock_idx0_monitor myproject_hls_deadlock_idx0_monitor_U (
    .clock(kernel_monitor_clock),
    .reset(kernel_monitor_reset),
    .axis_block_sigs(axis_block_sigs),
    .inst_idle_sigs(inst_idle_sigs),
    .inst_block_sigs(inst_block_sigs),
    .block(kernel_block)
);


always @ (kernel_block or kernel_monitor_reset) begin
    if (kernel_block == 1'b1 && kernel_monitor_reset == 1'b0) begin
        find_kernel_block = 1'b1;
    end
    else begin
        find_kernel_block = 1'b0;
    end
end
