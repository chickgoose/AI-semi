`timescale 1ns/1ps

// W2 physical wrapper for the complete A3 exact-prefix K2 scheduler plus P6.
// As for A2, the full-link measurement mode is explicitly always enabled.
module k2_w2_a3_p6_top (
  input  logic        ref_clk_i,
  input  logic        sample_clk_i,
  input  logic        rst_n,
  input  logic [15:0] load_i,
  output logic        bundle_valid_o,
  output logic        bundle_ready_o,
  output logic        bundle_commit_o,
  output logic [1:0]  grant_count_o,
  output logic [3:0]  grant_addr0_o,
  output logic [3:0]  grant_addr1_o,
  output logic [1:0]  policy_microsteps_o,
  output logic        bundle_protocol_error_o,
  output logic        p6_clk_o,
  output logic [4:0]  p6_data_o,
  output logic [1:0]  retire_valid_o,
  output logic [3:0]  retire_addr0_o,
  output logic [3:0]  retire_addr1_o,
  output logic        retire_protocol_error_o,
  output logic        drain_idle_o
);
  a3_exact_scalar_prefix_k2_p6_top composition (
    .ref_clk_i,
    .sample_clk_i,
    .rst_n,
    .link_enable_i(1'b1),
    .source_pending_i(load_i),
    .bundle_valid_o,
    .bundle_ready_o,
    .bundle_commit_o,
    .grant_count_o,
    .grant_addr0_o,
    .grant_addr1_o,
    .policy_microsteps_o,
    .bundle_protocol_error_o,
    .p6_clk_o,
    .p6_data_o,
    .retire_valid_o,
    .retire_addr0_o,
    .retire_addr1_o,
    .retire_protocol_error_o,
    .drain_idle_o
  );
endmodule
