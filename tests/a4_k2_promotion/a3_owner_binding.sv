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
  a3_exact_scalar_prefix_k2 owner (
    .clk            (clk),
    .rst            (rst),
    .source_pending (source_pending),
    .grant_count    (grant_count),
    .lane0_addr     (grant_addr0),
    .lane1_addr     (grant_addr1),
    .bundle_ready   (bundle_ready)
  );

  // The owner reserves its registered offer.  At this wrapper boundary the
  // testbench keeps source_pending asserted until the atomic commit.
  always @* begin
    drain_idle = (source_pending == 16'b0) && (grant_count == 0);
  end
endmodule
