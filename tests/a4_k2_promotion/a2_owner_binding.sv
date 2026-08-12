`timescale 1ns/1ps

module a4_k2_owner (
  input  logic        clk,
  input  logic        rst,
  input  logic [15:0] source_pending,
  output logic [1:0]  grant_count,
  output logic [3:0]  grant_addr0,
  output logic [3:0]  grant_addr1,
  input  logic        bundle_ready,
  output logic        drain_idle
);
  logic [15:0] unused_grant_bitmap;

  a2_batched_iwrr_k2 owner (
    .clk          (clk),
    .rst          (rst),
    .req          (source_pending),
    .grant_count  (grant_count),
    .grant_addr0  (grant_addr0),
    .grant_addr1  (grant_addr1),
    .grant_bitmap (unused_grant_bitmap),
    .bundle_ready (bundle_ready),
    .drain_idle   (drain_idle)
  );
endmodule
