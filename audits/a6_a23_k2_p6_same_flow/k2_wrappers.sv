module a6_a2_k2_normalized_top (
  input logic clk, input logic rst, input logic [15:0] pending,
  input logic bundle_ready, output logic [1:0] grant_count,
  output logic [3:0] grant_addr0, output logic [3:0] grant_addr1
);
  logic [15:0] charged_grant_bitmap;
  logic charged_drain_idle;
  a2_batched_iwrr_k2 scheduler (
    .clk, .rst, .req(pending), .grant_count, .grant_addr0, .grant_addr1,
    .grant_bitmap(charged_grant_bitmap), .bundle_ready,
    .drain_idle(charged_drain_idle)
  );
endmodule

module a6_a3_k2_normalized_top (
  input logic clk, input logic rst, input logic [15:0] pending,
  input logic bundle_ready, output logic [1:0] grant_count,
  output logic [3:0] grant_addr0, output logic [3:0] grant_addr1
);
  a3_exact_scalar_prefix_k2 scheduler (
    .clk, .rst, .source_pending(pending), .grant_count,
    .lane0_addr(grant_addr0), .lane1_addr(grant_addr1), .bundle_ready
  );
endmodule
