// Test-only native-boundary causality monitor. A level request creates at most
// one credit per source, regardless of how many cycles it remains asserted.
// Each raw result bit consumes that credit. This state is verification-only and
// must never reconstruct an event or feed the production binding/DUT.
`timescale 1ns/1ps

module aer_cluster2_causal_credit_monitor #(
  parameter int NUM_SOURCES = 16
) (
  input logic                   clk,
  input logic                   rst,
  input logic [NUM_SOURCES-1:0] native_req,
  input logic [NUM_SOURCES-1:0] native_result_mask
);
  logic [NUM_SOURCES-1:0] sampled_request_credit;

  always_ff @(posedge clk) begin
    if (rst) begin
      sampled_request_credit <= '0;
    end else begin
      if ((native_req & native_result_mask) != '0)
        $fatal(1,
          "GANGHEE_CLUSTER2_CAUSAL_CREDIT seam_req_result_overlap mask=%h",
          native_req & native_result_mask);
      if ((native_result_mask & ~sampled_request_credit) != '0)
        $fatal(1,
          "GANGHEE_CLUSTER2_CAUSAL_CREDIT raw_without_credit mask=%h credit=%h",
          native_result_mask, sampled_request_credit);

      // A held level is one outstanding occurrence, so credit saturates at one.
      // The assertion above makes the normalized-seam exclusion explicit: a
      // current raw result must mask that source's request before this edge.
      sampled_request_credit <=
        (sampled_request_credit & ~native_result_mask) | native_req;
    end
  end
endmodule
