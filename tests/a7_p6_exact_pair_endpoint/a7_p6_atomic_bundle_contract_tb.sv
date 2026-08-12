`timescale 1ns/1ps

module a7_p6_atomic_bundle_contract_tb;
  logic ref_clk = 1'b0;
  logic sample_clk = 1'b0;
  logic rst_n = 1'b0;
  logic bundle_valid = 1'b0;
  logic [1:0] grant_count = 2'd0;
  logic [3:0] grant_addr0 = 4'd0;
  logic [3:0] grant_addr1 = 4'd0;

  logic p6_ready, p6_commit, p6_error;
  logic [1:0] p6_steps;
  logic p6_clk;
  logic [4:0] p6_data;
  logic [1:0] p6_retire_valid;
  logic [3:0] p6_retire_addr0, p6_retire_addr1;
  logic p6_retire_error, p6_drain;

  logic ref_ready, ref_commit, ref_error;
  logic [1:0] ref_steps;
  logic ref_strobe, ref_pair;
  logic [3:0] ref_link_addr0, ref_link_addr1;
  logic [1:0] ref_retire_valid;
  logic [3:0] ref_retire_addr0, ref_retire_addr1;
  logic ref_retire_error, ref_drain;

  integer cycle = 0;
  integer bundle_commits = 0;
  integer empty_commits = 0;
  integer policy_microsteps = 0;
  integer retired_events = 0;
  integer expected_head = 0;
  integer expected_tail = 0;
  integer expected_count [0:127];
  integer expected_addr0 [0:127];
  integer expected_addr1 [0:127];
  integer expected_cycle [0:127];

  logic sampled_commit;
  logic [1:0] sampled_steps;
  logic [1:0] sampled_count;
  logic [3:0] sampled_addr0, sampled_addr1;
  logic prior_stalled = 1'b0;
  logic [1:0] held_count;
  logic [3:0] held_addr0, held_addr1;

  always #8 ref_clk = ~ref_clk;
  initial begin
    #4;
    forever #8 sample_clk = ~sample_clk;
  end

  a7_p6_atomic_bundle_adapter dut (
    .ref_clk_i(ref_clk), .sample_clk_i(sample_clk), .rst_n,
    .bundle_valid_i(bundle_valid), .grant_count_i(grant_count),
    .grant_addr0_i(grant_addr0), .grant_addr1_i(grant_addr1),
    .bundle_ready_o(p6_ready), .bundle_commit_o(p6_commit),
    .policy_microsteps_o(p6_steps), .bundle_protocol_error_o(p6_error),
    .p6_clk_o(p6_clk), .p6_data_o(p6_data),
    .retire_valid_o(p6_retire_valid), .retire_addr0_o(p6_retire_addr0),
    .retire_addr1_o(p6_retire_addr1),
    .retire_protocol_error_o(p6_retire_error), .drain_idle_o(p6_drain)
  );

  a7_p6_atomic_bundle_parallel_reference parallel_ref (
    .ref_clk_i(ref_clk), .sample_clk_i(sample_clk), .rst_n,
    .bundle_valid_i(bundle_valid), .grant_count_i(grant_count),
    .grant_addr0_i(grant_addr0), .grant_addr1_i(grant_addr1),
    .bundle_ready_o(ref_ready), .bundle_commit_o(ref_commit),
    .policy_microsteps_o(ref_steps), .bundle_protocol_error_o(ref_error),
    .parallel_strobe_o(ref_strobe), .parallel_pair_o(ref_pair),
    .parallel_addr0_o(ref_link_addr0), .parallel_addr1_o(ref_link_addr1),
    .retire_valid_o(ref_retire_valid), .retire_addr0_o(ref_retire_addr0),
    .retire_addr1_o(ref_retire_addr1),
    .retire_protocol_error_o(ref_retire_error), .drain_idle_o(ref_drain)
  );

  task automatic fail(input string marker, input string reason);
    begin
      $display("%s %s", marker, reason);
      $fatal(1, "%s", reason);
    end
  endtask

  always @(posedge ref_clk) begin
    sampled_commit = bundle_valid && p6_ready;
    sampled_steps = p6_steps;
    sampled_count = grant_count;
    sampled_addr0 = grant_addr0;
    sampled_addr1 = grant_addr1;
    cycle = cycle + 1;

    // The source-side requirement includes the eventual acceptance edge: an
    // offer stalled on the prior edge must not change before it commits.
    if (prior_stalled &&
        ((grant_count !== held_count) || (grant_addr0 !== held_addr0) ||
         (grant_addr1 !== held_addr1) || !bundle_valid))
      fail("A7_P6_BUNDLE_STABILITY_FAIL", "bundle changed while ready was low");
    // Illegal count-three attempts are rejected, not accepted transactions,
    // so they are outside the held-valid source obligation.
    prior_stalled = bundle_valid && (grant_count != 2'd3) && !p6_ready;
    held_count = grant_count;
    held_addr0 = grant_addr0;
    held_addr1 = grant_addr1;

    if (p6_ready !== ref_ready || p6_commit !== ref_commit ||
        p6_steps !== ref_steps || p6_error !== ref_error)
      fail("A7_P6_ATOMIC_REFERENCE_FAIL", "P6/parallel scheduler seam mismatch");
    if (p6_commit !== sampled_commit)
      fail("A7_P6_ATOMIC_COMMIT_FAIL", "commit differs from valid and bundle_ready");
    if (sampled_steps !== (sampled_commit ? sampled_count : 2'd0))
      fail("A7_P6_PARTIAL_COMMIT_MUTATION_CAUGHT",
           "policy did not advance by exactly grant_count microsteps");

    #1;
    if (p6_retire_valid !== ref_retire_valid ||
        p6_retire_addr0 !== ref_retire_addr0 ||
        p6_retire_addr1 !== ref_retire_addr1 ||
        p6_retire_error !== ref_retire_error)
      fail("A7_P6_ATOMIC_REFERENCE_FAIL", "P6/parallel retirement mismatch");
    if (p6_retire_error || ref_retire_error)
      fail("A7_P6_ATOMIC_REFERENCE_FAIL", "unexpected retirement error");

    if (p6_retire_valid != 2'b00) begin
      if (expected_head == expected_tail)
        fail("A7_P6_ATOMIC_COMMIT_FAIL", "retirement without nonempty bundle commit");
      if (p6_retire_valid !== ((expected_count[expected_head] == 2) ?
                               2'b11 : 2'b01) ||
          p6_retire_addr0 !== expected_addr0[expected_head][3:0] ||
          p6_retire_addr1 !== expected_addr1[expected_head][3:0])
        fail("A7_P6_ATOMIC_ORDER_FAIL", "partial, duplicated, or reordered bundle");
      if (cycle != expected_cycle[expected_head] + 1)
        fail("A7_P6_ATOMIC_TIMING_FAIL", "accepted-to-retire gap changed");
      retired_events = retired_events + expected_count[expected_head];
      expected_head = expected_head + 1;
    end

    if (sampled_commit) begin
      bundle_commits = bundle_commits + 1;
      policy_microsteps = policy_microsteps + sampled_steps;
      if (sampled_count == 0)
        empty_commits = empty_commits + 1;
      else begin
        expected_count[expected_tail] = sampled_count;
        expected_addr0[expected_tail] = sampled_addr0;
        expected_addr1[expected_tail] = (sampled_count == 2) ?
                                        sampled_addr1 : 0;
        expected_cycle[expected_tail] = cycle;
        expected_tail = expected_tail + 1;
      end
    end
  end

  task automatic drive(input logic valid, input logic [1:0] count,
                       input logic [3:0] addr0, input logic [3:0] addr1);
    begin
      @(negedge ref_clk);
      bundle_valid = valid;
      grant_count = count;
      grant_addr0 = addr0;
      grant_addr1 = addr1;
    end
  endtask

  task automatic wait_drain;
    begin
      drive(1'b0, 2'd0, 4'd0, 4'd0);
      while (!p6_drain || !ref_drain || expected_head != expected_tail)
        @(posedge ref_clk);
      @(negedge ref_clk);
    end
  endtask

  initial begin
    // Legal pair is held unchanged across reset and the charged arm stall.
    drive(1'b1, 2'd2, 4'h0, 4'h1);
    repeat (2) @(posedge ref_clk);
    if (p6_ready || p6_commit || p6_steps != 0)
      fail("A7_P6_ATOMIC_STALL_FAIL", "bundle committed during reset");
    @(negedge ref_clk);
    rst_n = 1'b1;
    @(posedge ref_clk);
    if (sampled_commit)
      fail("A7_P6_ATOMIC_STALL_FAIL", "bundle committed on reset-arm edge");
    @(posedge ref_clk);

    // Valid count-zero offers handshake atomically but launch no link cell and
    // advance no policy state.  Addresses are don't-care payload in this case.
    drive(1'b1, 2'd0, 4'ha, 4'h5);
    @(posedge ref_clk);
    drive(1'b1, 2'd1, 4'hf, 4'h9);
    @(posedge ref_clk);

    // A5 evaluator adversaries represented at the normalized link seam:
    // distinct ordered same-row pairs and later generations remain ordered.
    drive(1'b1, 2'd2, 4'h4, 4'h5);
    @(posedge ref_clk);
    drive(1'b1, 2'd2, 4'h8, 4'h9);
    @(posedge ref_clk);
    drive(1'b1, 2'd2, 4'h0, 4'h8);
    @(posedge ref_clk);
    wait_drain();

    if (empty_commits != 1)
      fail("A7_P6_ATOMIC_ZERO_FAIL", "count-zero bundle was not one no-op commit");

    // K2 overflow and invalid/nonzero offers fail closed as whole bundles.
    drive(1'b1, 2'd3, 4'h1, 4'h2);
    #1;
    if (p6_ready || !p6_error || p6_commit || p6_steps != 0)
      fail("A7_P6_ATOMIC_OVERFLOW_FAIL", "count-three bundle did not fail closed");
    drive(1'b0, 2'd2, 4'h1, 4'h2);
    #1;
    if (p6_ready || !p6_error || p6_commit || p6_steps != 0)
      fail("A7_P6_ATOMIC_OVERFLOW_FAIL", "invalid nonzero bundle did not fail closed");
    wait_drain();

    // Reset only after drain; no accepted record or policy step may reappear.
    @(negedge ref_clk);
    rst_n = 1'b0;
    repeat (2) @(posedge ref_clk);
    if (p6_retire_valid != 0 || ref_retire_valid != 0)
      fail("A7_P6_ATOMIC_RESET_FAIL", "retirement visible during reset");
    @(negedge ref_clk);
    rst_n = 1'b1;
    repeat (2) @(posedge ref_clk);
    drive(1'b1, 2'd2, 4'hc, 4'h3);
    @(posedge ref_clk);
    wait_drain();

    if (expected_head != expected_tail ||
        retired_events != policy_microsteps)
      fail("A7_P6_ATOMIC_COMMIT_FAIL", "commit/retire/microstep conservation failed");
    $display("A7_P6_ATOMIC_BUNDLE_PASS bundles=%0d empty=%0d events=%0d partial_scheduler_commits=0",
             bundle_commits, empty_commits,
             policy_microsteps);
    $finish;
  end
endmodule
