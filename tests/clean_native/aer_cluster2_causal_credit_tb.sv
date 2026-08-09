`timescale 1ns/1ps

module aer_cluster2_causal_credit_tb;
  localparam int NUM_SOURCES = 16;
  localparam int SOURCE = 5;

  logic clk = 1'b0;
  logic rst;
  logic [NUM_SOURCES-1:0] source_valid;
  logic [NUM_SOURCES-1:0] raw_result_mask;
  wire  [NUM_SOURCES-1:0] native_req =
    source_valid & ~raw_result_mask;
  wire  [NUM_SOURCES-1:0] source_ready =
    source_valid & raw_result_mask;

  integer sampled_request_episodes = 0;
  integer raw_results = 0;
  integer accepted = 0;
  logic previous_native_req;

  always #5 clk = ~clk;

  aer_cluster2_causal_credit_monitor #(
    .NUM_SOURCES(NUM_SOURCES)
  ) monitor (
    .clk(clk),
    .rst(rst),
    .native_req(native_req),
    .native_result_mask(raw_result_mask)
  );

  // Test-only occurrence bookkeeping. Both occurrences have exactly the same
  // mandatory address; counters, not payload bits, distinguish A from B.
  always @(posedge clk) begin
    if (rst) begin
      previous_native_req <= 1'b0;
    end else begin
      if (native_req[SOURCE] && !previous_native_req)
        sampled_request_episodes = sampled_request_episodes + 1;
      previous_native_req <= native_req[SOURCE];
      if (raw_result_mask[SOURCE])
        raw_results = raw_results + 1;
      if (source_ready[SOURCE]) begin
        accepted = accepted + 1;
        source_valid[SOURCE] <= 1'b0;
      end
    end
  end

  task automatic present_occurrence;
    begin
      if (source_valid[SOURCE])
        $fatal(1, "CAUSAL_CREDIT_TB violated one-outstanding source contract");
      source_valid[SOURCE] = 1'b1;
    end
  endtask

  initial begin
    rst = 1'b1;
    source_valid = '0;
    raw_result_mask = '0;
    previous_native_req = 1'b0;
    repeat (2) @(posedge clk);
    @(negedge clk);
    rst = 1'b0;

    // Occurrence A: one sampled level-request episode creates one credit.
    present_occurrence();
    @(posedge clk);
    @(negedge clk);
    raw_result_mask[SOURCE] = 1'b1;
    @(posedge clk);

`ifdef AER_CAUSAL_IMMEDIATE_REPEAT
    // A clears at the preceding edge. B is offered at the earliest legal
    // driver negedge while the faulty raw A bitmap is retained. B is never
    // sampled because the stale bitmap masks native_req, so the next edge must
    // consume no credit and fail closed.
    @(negedge clk);
    present_occurrence();
    @(posedge clk);
    @(negedge clk);
    $fatal(1, "CAUSAL_CREDIT_TB immediate repeat unexpectedly survived");
`elsif AER_CAUSAL_DELAYED_STALE
    // Insert a complete low observation cycle. Consecutive-value comparison
    // cannot catch this stale result, but the unconsumed-credit rule must.
    @(negedge clk);
    raw_result_mask = '0;
    @(posedge clk);
    @(negedge clk);
    raw_result_mask[SOURCE] = 1'b1;
    @(posedge clk);
    @(negedge clk);
    $fatal(1, "CAUSAL_CREDIT_TB delayed stale unexpectedly survived");
`else
    // Legal fastest retrigger: raw drops before B is offered, allowing B to be
    // sampled on the next edge and to create its own credit.
    @(negedge clk);
    raw_result_mask = '0;
    present_occurrence();
    @(posedge clk);
    @(negedge clk);
    raw_result_mask[SOURCE] = 1'b1;
    @(posedge clk);
    @(negedge clk);
    raw_result_mask = '0;

    // A second reset occurs only after both accepted occurrences have drained.
    rst = 1'b1;
    repeat (2) @(posedge clk);
    @(negedge clk);
    rst = 1'b0;
    present_occurrence();
    @(posedge clk);
    @(negedge clk);
    raw_result_mask[SOURCE] = 1'b1;
    @(posedge clk);
    @(negedge clk);
    raw_result_mask = '0;

    if ((sampled_request_episodes != 3) || (raw_results != 3) ||
        (accepted != 3))
      $fatal(1,
        "CAUSAL_CREDIT_TB count mismatch sampled=%0d raw=%0d accepted=%0d",
        sampled_request_episodes, raw_results, accepted);
    $display("GANGHEE_CLUSTER2_CAUSAL_CREDIT_PASS sampled=3 raw=3 accepted=3 reset_after_drain=1");
    $finish;
`endif
  end
endmodule
