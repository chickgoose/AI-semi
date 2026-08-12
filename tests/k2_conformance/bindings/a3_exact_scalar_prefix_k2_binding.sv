`timescale 1ns/1ps

// Optional owner-side shim for the A3 candidate present in this repository.
// It changes no candidate RTL and contains no arbitration policy.
module k2_candidate_binding (
  input  logic        clk,
  input  logic        rst,
  input  logic [15:0] source_pending,
  output logic [1:0]  grant_count,
  output logic [3:0]  grant_addr0,
  output logic [3:0]  grant_addr1,
  input  logic        bundle_ready,
  output logic        drain_idle
);
  a3_exact_scalar_prefix_k2 owner (
    .clk(clk), .rst(rst), .source_pending(source_pending),
    .grant_count(grant_count), .lane0_addr(grant_addr0),
    .lane1_addr(grant_addr1), .bundle_ready(bundle_ready)
  );
  assign drain_idle = (source_pending == 0) && (grant_count == 0);
endmodule
