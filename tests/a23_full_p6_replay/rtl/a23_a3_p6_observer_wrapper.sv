`timescale 1ns/1ps

// Observation-only normalization of the actual A3 scheduler + P6 top.
module a23_a3_p6_observer_wrapper (
  input  logic        ref_clk_i,
  input  logic        sample_clk_i,
  input  logic        rst_n,
  input  logic        link_enable_i,
  input  logic [15:0] source_pending_i,
  output logic [15:0] source_accept_o,
  output logic        accept_valid_o,
  output logic [1:0]  accept_count_o,
  output logic [3:0]  accept_addr0_o,
  output logic [3:0]  accept_addr1_o,
  output logic [1:0]  retire_valid_o,
  output logic [3:0]  retire_addr0_o,
  output logic [3:0]  retire_addr1_o,
  output logic        protocol_error_o,
  output logic        drain_idle_o
);
  logic bundle_valid;
  logic bundle_ready;
  logic bundle_commit;
  logic [1:0] grant_count;
  logic [3:0] grant_addr0;
  logic [3:0] grant_addr1;
  logic [1:0] policy_microsteps;
  logic bundle_protocol_error;
  logic retire_protocol_error;
  logic p6_clk;
  logic [4:0] p6_data;

  a3_exact_scalar_prefix_k2_p6_top dut (
    .ref_clk_i, .sample_clk_i, .rst_n, .link_enable_i, .source_pending_i,
    .bundle_valid_o(bundle_valid), .bundle_ready_o(bundle_ready),
    .bundle_commit_o(bundle_commit), .grant_count_o(grant_count),
    .grant_addr0_o(grant_addr0), .grant_addr1_o(grant_addr1),
    .policy_microsteps_o(policy_microsteps),
    .bundle_protocol_error_o(bundle_protocol_error), .p6_clk_o(p6_clk),
    .p6_data_o(p6_data), .retire_valid_o, .retire_addr0_o,
    .retire_addr1_o, .retire_protocol_error_o(retire_protocol_error),
    .drain_idle_o
  );

  always_comb begin
    source_accept_o = '0;
    if (bundle_commit) begin
      source_accept_o[grant_addr0] = 1'b1;
      if (grant_count == 2'd2)
        source_accept_o[grant_addr1] = 1'b1;
    end
  end

  assign accept_valid_o = bundle_commit;
  assign accept_count_o = bundle_commit ? grant_count : 2'd0;
  assign accept_addr0_o = grant_addr0;
  assign accept_addr1_o = grant_addr1;
  assign protocol_error_o = bundle_protocol_error || retire_protocol_error ||
                            (bundle_commit &&
                             (policy_microsteps != grant_count));
endmodule
