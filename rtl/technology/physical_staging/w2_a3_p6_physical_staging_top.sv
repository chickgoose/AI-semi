`timescale 1ns/1ps

module w2_a3_p6_physical_staging_top (
  input  logic        ref_clk_i,
  input  logic        sample_clk_i,
  input  logic        rst_n,
  input  logic [15:0] source_pending_i,
  output logic [15:0] source_accept_o,
  output logic        link_clk_o,
  output logic [4:0]  link_data_o,
  output logic [1:0]  retire_valid_o,
  output logic [3:0]  retire_addr0_o,
  output logic [3:0]  retire_addr1_o,
  output logic        drain_idle_o,
  output logic        protocol_error_o
);
  logic adapter_ready, adapter_idle, bundle_valid, bundle_commit;
  logic [1:0] grant_count, policy_microsteps;
  logic [3:0] grant_addr0, grant_addr1;
  logic bundle_protocol_error, retire_protocol_error;
  always_comb begin
    source_accept_o = 16'd0;
    if (bundle_commit) begin
      source_accept_o[grant_addr0] = 1'b1;
      if (grant_count == 2'd2) source_accept_o[grant_addr1] = 1'b1;
    end
  end
  assign bundle_valid = (grant_count != 2'd0);
  a3_exact_scalar_prefix_k2 scheduler (
    .clk(ref_clk_i), .rst(!rst_n), .source_pending(source_pending_i),
    .grant_count, .lane0_addr(grant_addr0), .lane1_addr(grant_addr1),
    .bundle_ready(adapter_ready)
  );
  w2_p6_atomic_bundle_adapter_tech link (
    .ref_clk_i, .sample_clk_i, .rst_n,
    .bundle_valid_i(bundle_valid),
    .grant_count_i(bundle_valid ? grant_count : 2'd0),
    .grant_addr0_i(grant_addr0), .grant_addr1_i(grant_addr1),
    .bundle_ready_o(adapter_ready), .bundle_commit_o(bundle_commit),
    .policy_microsteps_o(policy_microsteps),
    .bundle_protocol_error_o(bundle_protocol_error),
    .p6_clk_o(link_clk_o), .p6_data_o(link_data_o),
    .retire_valid_o, .retire_addr0_o, .retire_addr1_o,
    .retire_protocol_error_o(retire_protocol_error),
    .drain_idle_o(adapter_idle)
  );
  assign protocol_error_o = bundle_protocol_error || retire_protocol_error ||
                            (bundle_commit && (policy_microsteps != grant_count));
  assign drain_idle_o = rst_n && !(|source_pending_i) && adapter_idle &&
                        !bundle_valid && !(|retire_valid_o) &&
                        !protocol_error_o;
endmodule
