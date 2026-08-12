module a6_a4_k2_normalized_top (
  input logic clk, input logic rst, input logic [15:0] pending,
  input logic bundle_ready, output logic [1:0] grant_count,
  output logic [3:0] grant_addr0, output logic [3:0] grant_addr1
);
  logic [15:0] charged_source_ready;
  logic [7:0] packed_grant_addr;
  logic charged_drain_idle;

  a4_paired_cortical_column_k2 scheduler (
    .clk, .rst_n(~rst), .source_valid(pending),
    .source_ready(charged_source_ready), .grant_count,
    .grant_addr(packed_grant_addr), .bundle_ready,
    .drain_idle(charged_drain_idle)
  );

  assign grant_addr0 = packed_grant_addr[3:0];
  assign grant_addr1 = packed_grant_addr[7:4];
endmodule
