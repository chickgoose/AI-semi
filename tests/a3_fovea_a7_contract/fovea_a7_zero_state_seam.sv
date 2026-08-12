// Test-only zero-state composition seam. This is not candidate RTL.
module a3_fovea_a7_zero_state_seam (
  input  logic        ref_clk_i,
  input  logic        sample_clk_i,
  input  logic        rst_n,
  input  logic [15:0] source_valid_i,
  output logic [15:0] source_ready_o,
  output logic        burst_clk_o,
  output logic [1:0]  burst_data_o,
  output logic [3:0]  retire_addr_o,
  output logic        retire_valid_o,
  output logic        drain_idle_o
);
  logic [15:0] fovea_req;
  logic        fovea_valid;
  logic [3:0]  fovea_addr;
  logic [15:0] current_result_mask;
  logic        a7_ready;

  assign current_result_mask = fovea_valid ? (16'b1 << fovea_addr) : 16'b0;
  assign fovea_req = source_valid_i & ~current_result_mask;
  assign source_ready_o = source_valid_i & current_result_mask;

  aer_tx16_trad_rowcol_fovea #(.WEIGHT(5)) fovea (
    .clk(ref_clk_i), .rst(~rst_n), .req(fovea_req),
    .valid(fovea_valid), .addr(fovea_addr));

  a7_r1_candidate_endpoint link (
    .ref_clk_i(ref_clk_i), .sample_clk_i(sample_clk_i), .rst_n(rst_n),
    .event_valid_i(fovea_valid), .event_addr_i(fovea_addr),
    .event_ready_o(a7_ready), .burst_clk_o(burst_clk_o),
    .burst_data_o(burst_data_o), .retire_addr_o(retire_addr_o),
    .retire_valid_o(retire_valid_o), .drain_idle_o(drain_idle_o));

  // With the common reset epoch, FOVEA's registered valid cannot precede A7's
  // charged post-reset arming edge. After arming, R1 ready remains asserted.
endmodule
