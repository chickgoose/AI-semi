`timescale 1ns/1ps

// Integrated digital-only A3 exact-scalar-prefix K2 to P6 link top.
//
// link_enable_i is a queue-free admission gate.  When it is low, the A3
// owner's registered atomic offer is held and the P6 frontend sees the legal
// invalid/count-zero encoding.  No scheduler state advances and no link cell
// launches.  Retirement is always-ready, as required by the P6 endpoint.
module a3_exact_scalar_prefix_k2_p6_top (
  input  logic        ref_clk_i,
  input  logic        sample_clk_i,
  input  logic        rst_n,
  input  logic        link_enable_i,
  input  logic [15:0] source_pending_i,

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
  logic adapter_ready;
  logic adapter_drain_idle;
  logic adapter_bundle_valid;
  logic [1:0] adapter_grant_count;

  assign bundle_valid_o = (grant_count_o != 2'd0);
  assign bundle_ready_o = link_enable_i && adapter_ready;

  // The P6 frontend requires invalid cycles to carry count zero.  Gating
  // valid and count together makes a disabled link a legal backpressure
  // condition rather than an invalid/nonzero protocol attempt.
  assign adapter_bundle_valid = link_enable_i && bundle_valid_o;
  assign adapter_grant_count = adapter_bundle_valid ? grant_count_o : 2'd0;

  a3_exact_scalar_prefix_k2 scheduler (
    .clk(ref_clk_i),
    .rst(!rst_n),
    .source_pending(source_pending_i),
    .grant_count(grant_count_o),
    .lane0_addr(grant_addr0_o),
    .lane1_addr(grant_addr1_o),
    .bundle_ready(bundle_ready_o)
  );

  a7_p6_atomic_bundle_adapter link (
    .ref_clk_i,
    .sample_clk_i,
    .rst_n,
    .bundle_valid_i(adapter_bundle_valid),
    .grant_count_i(adapter_grant_count),
    .grant_addr0_i(grant_addr0_o),
    .grant_addr1_i(grant_addr1_o),
    .bundle_ready_o(adapter_ready),
    .bundle_commit_o,
    .policy_microsteps_o,
    .bundle_protocol_error_o,
    .p6_clk_o,
    .p6_data_o,
    .retire_valid_o,
    .retire_addr0_o,
    .retire_addr1_o,
    .retire_protocol_error_o,
    .drain_idle_o(adapter_drain_idle)
  );

  // A registered but not yet committed scheduler offer is internal work at
  // this integrated boundary even if the physical endpoint itself is idle.
  assign drain_idle_o = adapter_drain_idle && !bundle_valid_o;

`ifndef SYNTHESIS
  always @(posedge ref_clk_i) begin
    if (rst_n) begin
      if (bundle_commit_o !== (bundle_valid_o && bundle_ready_o))
        $fatal(1, "A3_P6 bundle commit diverged from owner atomic fire");
      if (policy_microsteps_o !==
          (bundle_commit_o ? grant_count_o : 2'd0))
        $fatal(1, "A3_P6 policy microstep conservation failure");
    end
  end
`endif
endmodule
