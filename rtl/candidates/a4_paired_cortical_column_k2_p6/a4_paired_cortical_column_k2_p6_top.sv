`timescale 1ns/1ps

// Digital-only A4 Paired-Cortical-Column K2 to exact atomic P6 integration.
//
// The A4 owner already freezes its complete ordered offer and every policy
// register while bundle_ready is low.  The minimal seam is therefore a
// queue-free admission gate: it adds no state and does not flatten, duplicate,
// or otherwise replace A4's paired-column/calendar/debt policy.
module a4_paired_cortical_column_k2_p6_top #(
  parameter int DEBT_WIDTH = 4
) (
  input  logic        ref_clk_i,
  input  logic        sample_clk_i,
  input  logic        rst_n,
  input  logic        link_enable_i,
  input  logic [15:0] source_pending_i,

  output logic [15:0] source_ready_o,
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
  logic [7:0] owner_grant_addr;
  logic owner_drain_idle;
  logic adapter_ready;
  logic adapter_drain_idle;
  logic adapter_bundle_valid;
  logic [1:0] adapter_grant_count;

  assign grant_addr0_o = owner_grant_addr[3:0];
  assign grant_addr1_o = owner_grant_addr[7:4];
  assign bundle_valid_o = (grant_count_o != 2'd0);
  assign bundle_ready_o = link_enable_i && adapter_ready;

  // Disabled admission is encoded as the P6 frontend's legal invalid/count-0
  // cycle.  Count and valid are gated together, so quiescing cannot be
  // mistaken for an illegal nonzero invalid offer.
  assign adapter_bundle_valid = link_enable_i && bundle_valid_o;
  assign adapter_grant_count = adapter_bundle_valid ? grant_count_o : 2'd0;

  a4_paired_cortical_column_k2 #(
    .DEBT_WIDTH(DEBT_WIDTH)
  ) scheduler (
    .clk(ref_clk_i),
    .rst_n,
    .source_valid(source_pending_i),
    .source_ready(source_ready_o),
    .grant_count(grant_count_o),
    .grant_addr(owner_grant_addr),
    .bundle_ready(bundle_ready_o),
    .drain_idle(owner_drain_idle)
  );

  a7_p6_atomic_bundle_adapter p6_adapter (
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

  // A held A4 offer is internal work even when the physical endpoint is idle.
  assign drain_idle_o = owner_drain_idle && adapter_drain_idle &&
                        !bundle_valid_o;
endmodule
