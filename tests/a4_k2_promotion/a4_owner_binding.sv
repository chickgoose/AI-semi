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
  logic [15:0] unused_source_ready;
  logic [7:0] owner_grant_addr;

  a4_paired_cortical_column_k2 owner (
    .clk          (clk),
    .rst_n        (~rst),
    .source_valid (source_pending),
    .source_ready (unused_source_ready),
    .grant_count  (grant_count),
    .grant_addr   (owner_grant_addr),
    .bundle_ready (bundle_ready),
    .drain_idle   (drain_idle)
  );

  always @* begin
    grant_addr0 = owner_grant_addr[3:0];
    grant_addr1 = owner_grant_addr[7:4];
  end
endmodule
