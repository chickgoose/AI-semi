`timescale 1ns/1ps

`ifndef K2_EXPECT_LATENCY
`define K2_EXPECT_LATENCY 0
`endif

// Candidate owners provide exactly one k2_candidate_binding with this seam:
//   clk, rst, source_pending, grant_count, grant_addr0, grant_addr1,
//   bundle_ready, drain_idle.
module k2_atomic_conformance_tb;
  localparam integer EXPECT_LATENCY = `K2_EXPECT_LATENCY;
  `include "k2_conformance_vectors.svh"

  logic clk = 0;
  logic rst = 1;
  logic [15:0] source_pending = 0;
  logic [1:0] sink_ready = 0;
  wire [1:0] grant_count;
  wire [3:0] grant_addr0;
  wire [3:0] grant_addr1;
  wire bundle_ready;
  wire drain_idle;

  integer edge_stamp = 0;
  integer commits = 0;
  logic [15:0] cohort_seen;
  logic [1:0] saved_count;
  logic [3:0] saved_addr0;
  logic [3:0] saved_addr1;

  always #5 clk = ~clk;
  always @(posedge clk) edge_stamp = edge_stamp + 1;

  // This is the unbuffered atomic retirement rule under test.  Count one uses
  // lane 0 only; count two cannot partially commit.
`ifdef K2_TB_MUT_COUNT1_USES_LANE1
  assign bundle_ready = (grant_count == 0) ? 1'b1 :
                        sink_ready[0] && sink_ready[1];
`else
  assign bundle_ready = (grant_count == 0) ? 1'b1 :
                        (grant_count == 1) ? sink_ready[0] :
                        sink_ready[0] && sink_ready[1];
`endif

  k2_candidate_binding candidate (
    .clk(clk), .rst(rst), .source_pending(source_pending),
    .grant_count(grant_count), .grant_addr0(grant_addr0),
    .grant_addr1(grant_addr1), .bundle_ready(bundle_ready),
    .drain_idle(drain_idle)
  );

  k2_conformance_oracle oracle (.*);

  always @(posedge clk) begin
    if (!rst && (grant_count != 0) && bundle_ready) begin
      commits = commits + 1;
      if (cohort_seen[grant_addr0])
        $fatal(1, "K2_TB duplicate/replayed lane0 addr=%0d", grant_addr0);
      cohort_seen[grant_addr0] = 1'b1;
      if (grant_count == 2) begin
        if (cohort_seen[grant_addr1])
          $fatal(1, "K2_TB duplicate/replayed lane1 addr=%0d", grant_addr1);
        cohort_seen[grant_addr1] = 1'b1;
      end
    end
  end

  function automatic logic pair_matches(
    input logic [15:0] mask,
    input logic [3:0] a0,
    input logic [3:0] a1
  );
    if ((^a0 === 1'bx) || (^a1 === 1'bx))
      pair_matches = 1'b0;
    else
      pair_matches = (a0 != a1) && mask[a0] && mask[a1];
  endfunction

  task automatic drive_request(input logic [15:0] mask);
    begin
      @(negedge clk);
      source_pending = mask;
      sink_ready = 0;
      #1;
    end
  endtask

  task automatic expect_offer_at_exact_latency(
    input logic [15:0] mask,
    input integer expected_count,
    input logic [3:0] singleton_addr,
    input [255:0] label
  );
    integer phase;
    begin
      for (phase = 0; phase <= EXPECT_LATENCY; phase = phase + 1) begin
        if (phase < EXPECT_LATENCY) begin
          if (grant_count !== 0)
            $fatal(1, "K2_TB %0s early offer phase=%0d expected_latency=%0d",
                   label, phase, EXPECT_LATENCY);
          @(posedge clk);
          #1;
        end else begin
          if (grant_count !== expected_count)
            $fatal(1, "K2_TB %0s count=%0d expected=%0d latency=%0d",
                   label, grant_count, expected_count, EXPECT_LATENCY);
          if ((expected_count == 1) && (grant_addr0 !== singleton_addr))
            $fatal(1, "K2_TB %0s singleton addr=%0d expected=%0d",
                   label, grant_addr0, singleton_addr);
          if ((expected_count == 2) &&
              !pair_matches(mask, grant_addr0, grant_addr1))
            $fatal(1, "K2_TB %0s pair mismatch %0d,%0d mask=%h",
                   label, grant_addr0, grant_addr1, mask);
          $display("K2_LATENCY_STAMP case=%0s request_phase=0 offer_phase=%0d edge=%0d",
                   label, phase, edge_stamp);
        end
      end
    end
  endtask

  task automatic clear_after_accept;
    begin
      @(negedge clk);
      source_pending = 0;
      sink_ready = 0;
      #1;
    end
  endtask

  task automatic reset_candidate;
    begin
      @(negedge clk);
      rst = 1;
      source_pending = 0;
      sink_ready = 0;
      @(posedge clk);
      #1;
      if (grant_count !== 0)
        $fatal(1, "K2_TB reset did not force count0");
      @(negedge clk);
      rst = 0;
      #1;
      if (drain_idle !== 1'b1)
        $fatal(1, "K2_TB reset release was not drained");
      cohort_seen = 0;
    end
  endtask

  initial begin
    if ((EXPECT_LATENCY < 0) || (EXPECT_LATENCY > 8))
      $fatal(1, "K2_TB unreasonable declared latency=%0d", EXPECT_LATENCY);
    cohort_seen = 0;
    repeat (2) @(posedge clk);
    reset_candidate();

    // count0 and truthful idle.
    repeat (2) begin
      @(posedge clk); #1;
      if ((grant_count !== 0) || (drain_idle !== 1'b1))
        $fatal(1, "K2_TB count0/idle failure");
    end

    // count1 must use only lane-0 readiness.  lane1 is intentionally blocked.
    drive_request(K2_VEC_SINGLE);
    expect_offer_at_exact_latency(K2_VEC_SINGLE, 1, 5, "count1");
    @(negedge clk);
    sink_ready = 2'b01;
    #1;
    if (bundle_ready !== 1'b1)
      $fatal(1, "K2_TB count1 incorrectly depends on lane1 ready");
    @(posedge clk); #1;
    clear_after_accept();

    // A two-event offer must reject every partial-ready combination and hold
    // count, identities, and order until both lanes accept atomically.
    cohort_seen = 0;
    drive_request(K2_VEC_PAIR);
    expect_offer_at_exact_latency(K2_VEC_PAIR, 2, 0, "count2");
    saved_count = grant_count;
    saved_addr0 = grant_addr0;
    saved_addr1 = grant_addr1;
    @(negedge clk); sink_ready = 2'b01; #1;
    if (bundle_ready !== 1'b0)
      $fatal(1, "K2_TB count2 accepted lane0-only readiness");
    repeat (2) begin
      @(posedge clk); #1;
      if ((grant_count !== saved_count) || (grant_addr0 !== saved_addr0) ||
          (grant_addr1 !== saved_addr1))
        $fatal(1, "K2_TB count2 changed under partial ready");
    end
    @(negedge clk); sink_ready = 2'b10; #1;
    if (bundle_ready !== 1'b0)
      $fatal(1, "K2_TB count2 accepted lane1-only readiness");
    @(posedge clk); #1;
    if ((grant_count !== saved_count) || (grant_addr0 !== saved_addr0) ||
        (grant_addr1 !== saved_addr1))
      $fatal(1, "K2_TB count2 changed under lane1-only ready");
    @(negedge clk); sink_ready = 2'b11;
    @(posedge clk); #1;
    clear_after_accept();

    // Held offer plus back-to-back refill.  Future pending work may be visible,
    // but cannot perturb the reserved old cohort.  It must refill at the same
    // declared latency measured from the accepting edge.
    cohort_seen = 0;
    drive_request(K2_VEC_REFILL_OLD);
    expect_offer_at_exact_latency(K2_VEC_REFILL_OLD, 2, 0, "held");
    saved_addr0 = grant_addr0;
    saved_addr1 = grant_addr1;
    @(negedge clk);
    source_pending = K2_VEC_REFILL_OLD | K2_VEC_REFILL_NEW;
    repeat (3) begin
      @(posedge clk); #1;
      if ((grant_count !== 2) || (grant_addr0 !== saved_addr0) ||
          (grant_addr1 !== saved_addr1))
        $fatal(1, "K2_TB held offer changed with future pending work");
    end
    @(negedge clk); sink_ready = 2'b11;
    @(posedge clk); #1;
    source_pending = K2_VEC_REFILL_NEW;
    sink_ready = 0;
    #1;
    // A registered owner may have refilled on this edge; a combinational owner
    // sees the new mask immediately.  Both therefore use phase 0 here.
    if ((grant_count !== 2) ||
        !pair_matches(K2_VEC_REFILL_NEW, grant_addr0, grant_addr1))
      $fatal(1, "K2_TB back-to-back refill bubble/reorder count=%0d addrs=%0d,%0d",
             grant_count, grant_addr0, grant_addr1);
    $display("K2_LATENCY_STAMP case=back_to_back_refill accept_to_offer=0 edge=%0d",
             edge_stamp);
    @(negedge clk); sink_ready = 2'b11;
    @(posedge clk); #1;
    clear_after_accept();

    // Four identities require two ordered atomic commits.  The live-cohort
    // scoreboard rejects duplicates and phantoms without prescribing policy.
    cohort_seen = 0;
    drive_request(K2_VEC_IDENTITY);
    expect_offer_at_exact_latency(K2_VEC_IDENTITY, 2, 0, "identity_first");
    saved_addr0 = grant_addr0;
    saved_addr1 = grant_addr1;
    @(negedge clk); sink_ready = 2'b11;
    @(posedge clk); #1;
    source_pending = K2_VEC_IDENTITY &
                     ~((16'b1 << saved_addr0) | (16'b1 << saved_addr1));
    sink_ready = 0;
    #1;
    if ((grant_count !== 2) || !source_pending[grant_addr0] ||
        !source_pending[grant_addr1] || (grant_addr0 == grant_addr1))
      $fatal(1, "K2_TB identity second bundle invalid");
    @(negedge clk); sink_ready = 2'b11;
    @(posedge clk); #1;
    clear_after_accept();
    if ((cohort_seen & K2_VEC_IDENTITY) != K2_VEC_IDENTITY)
      $fatal(1, "K2_TB missing identity cohort seen=%h", cohort_seen);

    // Reset aborts a held offer.  No pre-reset identity may leak; the exact
    // post-reset sentinel latency must still match the declaration.
    cohort_seen = 0;
    drive_request(K2_VEC_PAIR);
    expect_offer_at_exact_latency(K2_VEC_PAIR, 2, 0, "pre_reset_hold");
    @(negedge clk); rst = 1; source_pending = 0; sink_ready = 0;
    @(posedge clk); #1;
    if (grant_count !== 0)
      $fatal(1, "K2_TB reset left stale offer");
    @(negedge clk); rst = 0; #1;
    drive_request(K2_VEC_RESET_SENTINEL);
    expect_offer_at_exact_latency(K2_VEC_RESET_SENTINEL, 1, 7,
                                  "post_reset_sentinel");
    @(negedge clk); sink_ready = 2'b01;
    @(posedge clk); #1;
    clear_after_accept();
    repeat (2) @(posedge clk);
    #1;
    if ((grant_count !== 0) || (drain_idle !== 1'b1))
      $fatal(1, "K2_TB final drain failed");

    $display("K2_ATOMIC_CONFORMANCE_PASS latency=%0d commits=%0d",
             EXPECT_LATENCY, commits);
    $finish;
  end
endmodule
