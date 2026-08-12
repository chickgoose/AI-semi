`timescale 1ns/1ps

module a3_exact_scalar_prefix_k2_p6_tb;
  logic ref_clk = 1'b0;
  logic sample_clk = 1'b0;
  logic rst_n = 1'b0;
  logic link_enable = 1'b1;
  logic [15:0] source_pending = 16'b0;

  logic bundle_valid;
  logic bundle_ready;
  logic bundle_commit;
  logic [1:0] grant_count;
  logic [3:0] grant_addr0;
  logic [3:0] grant_addr1;
  logic [1:0] policy_microsteps;
  logic bundle_protocol_error;
  logic p6_clk;
  logic [4:0] p6_data;
  logic [1:0] retire_valid;
  logic [3:0] retire_addr0;
  logic [3:0] retire_addr1;
  logic retire_protocol_error;
  logic drain_idle;

  integer cycle = 0;
  integer bundle_commits = 0;
  integer committed_events = 0;
  integer retired_events = 0;
  integer expected_head = 0;
  integer expected_tail = 0;
  integer expected_count [0:255];
  integer expected_addr0 [0:255];
  integer expected_addr1 [0:255];
  integer expected_cycle [0:255];

  logic last_sampled_ready = 1'b0;
  logic last_sampled_commit = 1'b0;
  logic [1:0] last_sampled_count = 2'd0;
  logic prior_stalled = 1'b0;
  logic [1:0] stalled_count;
  logic [3:0] stalled_addr0;
  logic [3:0] stalled_addr1;
  logic continuous_mode = 1'b0;
  integer continuous_commits = 0;
  integer previous_continuous_cycle = -1;

  always #8 ref_clk = ~ref_clk;
  initial begin
    #4;
    forever #8 sample_clk = ~sample_clk;
  end

  a3_exact_scalar_prefix_k2_p6_top dut (
    .ref_clk_i(ref_clk),
    .sample_clk_i(sample_clk),
    .rst_n,
    .link_enable_i(link_enable),
    .source_pending_i(source_pending),
    .bundle_valid_o(bundle_valid),
    .bundle_ready_o(bundle_ready),
    .bundle_commit_o(bundle_commit),
    .grant_count_o(grant_count),
    .grant_addr0_o(grant_addr0),
    .grant_addr1_o(grant_addr1),
    .policy_microsteps_o(policy_microsteps),
    .bundle_protocol_error_o(bundle_protocol_error),
    .p6_clk_o(p6_clk),
    .p6_data_o(p6_data),
    .retire_valid_o(retire_valid),
    .retire_addr0_o(retire_addr0),
    .retire_addr1_o(retire_addr1),
    .retire_protocol_error_o(retire_protocol_error),
    .drain_idle_o(drain_idle)
  );

  task automatic fail(input string marker, input string reason);
    begin
      $display("%s cycle=%0d %s", marker, cycle, reason);
      $fatal(1, "%s", reason);
    end
  endtask

  // End-to-end scoreboard.  It observes only the public atomic commit and
  // public retire ports; transport order/conservation are not inferred from
  // internal P6 state.
  always @(posedge ref_clk) begin
    cycle = cycle + 1;
    last_sampled_ready = bundle_ready;
    last_sampled_commit = bundle_commit;
    last_sampled_count = grant_count;

    if (!rst_n) begin
      prior_stalled = 1'b0;
    end else begin
      if (bundle_valid !== (grant_count != 2'd0))
        fail("A3_P6_COUNT_FAIL", "bundle valid/count encoding mismatch");
      if (grant_count > 2)
        fail("A3_P6_COUNT_FAIL", "owner emitted count greater than K2");
      if ((grant_count == 2) && (grant_addr0 == grant_addr1))
        fail("A3_P6_ORDER_FAIL", "owner emitted duplicate ordered pair");
      if (bundle_protocol_error || retire_protocol_error)
        fail("A3_P6_PROTOCOL_FAIL", "unexpected bundle or retire error");
      if (bundle_commit !== (bundle_valid && bundle_ready))
        fail("A3_P6_ATOMIC_FAIL", "commit was not one whole-bundle fire");
      if (policy_microsteps !==
          (bundle_commit ? grant_count : 2'd0))
        fail("A3_P6_CONSERVATION_FAIL", "microsteps differ from committed count");

      if (prior_stalled &&
          (!bundle_valid || grant_count !== stalled_count ||
           grant_addr0 !== stalled_addr0 ||
           grant_addr1 !== stalled_addr1))
        fail("A3_P6_STALL_FAIL", "atomic owner offer changed while stalled");
      prior_stalled = bundle_valid && !bundle_ready;
      stalled_count = grant_count;
      stalled_addr0 = grant_addr0;
      stalled_addr1 = grant_addr1;

      if (bundle_commit) begin
        expected_count[expected_tail] = grant_count;
        expected_addr0[expected_tail] = grant_addr0;
        expected_addr1[expected_tail] =
            (grant_count == 2) ? grant_addr1 : 0;
        expected_cycle[expected_tail] = cycle;
        expected_tail = expected_tail + 1;
        bundle_commits = bundle_commits + 1;
        committed_events = committed_events + grant_count;

        if (continuous_mode) begin
          if ((previous_continuous_cycle >= 0) &&
              (cycle != previous_continuous_cycle + 1))
            fail("A3_P6_CONTINUOUS_FAIL", "bundle commit bubble detected");
          if (grant_count != 2)
            fail("A3_P6_CONTINUOUS_FAIL", "persistent K2 offer was not a pair");
          previous_continuous_cycle = cycle;
          continuous_commits = continuous_commits + 1;
        end
      end
    end

    #1;
    if (!rst_n) begin
      if (retire_valid != 0 || p6_clk)
        fail("A3_P6_RESET_FAIL", "link activity visible during reset");
    end else if (retire_valid != 0) begin
      if (expected_head == expected_tail)
        fail("A3_P6_CONSERVATION_FAIL", "retirement without a commit");
      if (retire_valid !== ((expected_count[expected_head] == 2) ?
                            2'b11 : 2'b01))
        fail("A3_P6_ORDER_FAIL", "retire lane shape differs from bundle");
      if (retire_addr0 !== expected_addr0[expected_head][3:0] ||
          retire_addr1 !== expected_addr1[expected_head][3:0])
        fail("A3_P6_ORDER_FAIL", "P6 changed ordered bundle addresses");
      if (cycle != expected_cycle[expected_head] + 1)
        fail("A3_P6_CONTINUOUS_FAIL", "commit-to-retire latency changed");
      retired_events = retired_events + expected_count[expected_head];
      expected_head = expected_head + 1;
    end
  end

  task automatic drive(input logic [15:0] pending,
                       input logic enable);
    begin
      @(negedge ref_clk);
      source_pending = pending;
      link_enable = enable;
    end
  endtask

  task automatic wait_drained;
    integer timeout;
    begin
      timeout = 0;
      while (!(drain_idle && (expected_head == expected_tail))) begin
        @(posedge ref_clk);
        #2;
        timeout = timeout + 1;
        if (timeout > 12)
          fail("A3_P6_DRAIN_FAIL", "integrated drain timed out");
      end
      if (bundle_valid || p6_clk || retire_valid)
        fail("A3_P6_DRAIN_FAIL", "drain asserted with internal work");
    end
  endtask

  task automatic reset_after_drain;
    begin
      source_pending = 16'b0;
      link_enable = 1'b1;
      wait_drained();
      @(negedge ref_clk);
      rst_n = 1'b0;
      repeat (2) @(posedge ref_clk);
      #2;
      if (grant_count != 0 || bundle_commit || policy_microsteps != 0)
        fail("A3_P6_RESET_FAIL", "reset did not clear the integrated top");
      @(negedge ref_clk);
      rst_n = 1'b1;
      @(posedge ref_clk);
      #2;
      if (last_sampled_ready || last_sampled_commit)
        fail("A3_P6_STALL_FAIL", "P6 accepted on its reset-arm edge");
      if (retire_valid || grant_count)
        fail("A3_P6_RESET_FAIL", "reset release created phantom work");
    end
  endtask

  initial begin
    integer commits_before;
    integer events_before_reset;
    integer retires_before_reset;
    logic [1:0] held_count;
    logic [3:0] held_addr0;
    logic [3:0] held_addr1;

    // Establish the reset/drain contract, then prove exact empty behavior.
    repeat (2) @(posedge ref_clk);
    @(negedge ref_clk);
    rst_n = 1'b1;
    @(posedge ref_clk);
    #2;
    if (last_sampled_ready || grant_count != 0 || bundle_valid ||
        bundle_commit || policy_microsteps != 0 || retire_valid != 0)
      fail("A3_P6_COUNT0_FAIL", "count-zero arm cycle was not empty");
    @(posedge ref_clk);
    #2;
    if (grant_count != 0 || bundle_valid || bundle_commit || retire_valid != 0)
      fail("A3_P6_COUNT0_FAIL", "empty pending set produced traffic");
    $display("A3_P6_COUNT0_PASS");

    // Exact singleton: canonical address five, one commit, one retirement.
    reset_after_drain();
    drive(16'h0020, 1'b1);
    @(posedge ref_clk);
    #2;
    if (!(grant_count == 1 && grant_addr0 == 4'd5 && grant_addr1 == 0))
      fail("A3_P6_COUNT1_FAIL", "singleton offer was not exact address five");
    drive(16'h0000, 1'b1);
    @(posedge ref_clk);
    #2;
    if (!last_sampled_commit || last_sampled_count != 1)
      fail("A3_P6_COUNT1_FAIL", "singleton did not commit atomically");
    wait_drained();
    $display("A3_P6_COUNT1_PASS");

    // Exact ordered pair from the canonical sparse peripheral fallback.
    reset_after_drain();
    drive(16'h1001, 1'b1);
    @(posedge ref_clk);
    #2;
    if (!(grant_count == 2 && grant_addr0 == 4'd0 &&
          grant_addr1 == 4'd12))
      fail("A3_P6_COUNT2_FAIL", "canonical ordered pair was not 0 then 12");
    drive(16'h0000, 1'b1);
    @(posedge ref_clk);
    #2;
    if (!last_sampled_commit || last_sampled_count != 2)
      fail("A3_P6_COUNT2_FAIL", "pair did not commit as one bundle");
    wait_drained();
    $display("A3_P6_COUNT2_ORDER_PASS");

    // Queue-free whole-bundle stall.  Unrelated pending changes cannot alter
    // the owner's held registered offer or advance policy/link state.
    reset_after_drain();
    drive(16'h1001, 1'b0);
    @(posedge ref_clk);
    #2;
    if (!(grant_count == 2 && grant_addr0 == 0 && grant_addr1 == 12))
      fail("A3_P6_STALL_FAIL", "stall setup did not produce the known pair");
    held_count = grant_count;
    held_addr0 = grant_addr0;
    held_addr1 = grant_addr1;
    source_pending = 16'hf11f;
    repeat (4) begin
      @(posedge ref_clk);
      #2;
      if (bundle_ready || bundle_commit || policy_microsteps != 0 ||
          p6_clk || drain_idle || grant_count !== held_count ||
          grant_addr0 !== held_addr0 || grant_addr1 !== held_addr1)
        fail("A3_P6_STALL_FAIL", "stalled atomic state or link changed");
    end
    drive(16'h0000, 1'b1);
    @(posedge ref_clk);
    #2;
    if (!last_sampled_commit || last_sampled_count != 2)
      fail("A3_P6_STALL_FAIL", "held pair did not release atomically");
    wait_drained();
    $display("A3_P6_STALL_PASS");

    // Persistent full demand proves one pair commit per reference cycle and
    // the scoreboard proves the corresponding continuous ordered retirement.
    reset_after_drain();
    drive(16'hffff, 1'b1);
    @(posedge ref_clk);
    #2;
    if (grant_count != 2)
      fail("A3_P6_CONTINUOUS_FAIL", "persistent demand did not fill K2");
    continuous_mode = 1'b1;
    continuous_commits = 0;
    previous_continuous_cycle = -1;
    repeat (7) begin
      @(posedge ref_clk);
      #2;
    end
    drive(16'h0000, 1'b1);
    @(posedge ref_clk);
    #2;
    continuous_mode = 1'b0;
    if (continuous_commits != 8)
      fail("A3_P6_CONTINUOUS_FAIL", "did not observe eight continuous pairs");
    wait_drained();
    $display("A3_P6_CONTINUOUS_PASS bundles=%0d", continuous_commits);

    // A drained reset cannot replay accepted events or alter conservation.
    events_before_reset = committed_events;
    retires_before_reset = retired_events;
    commits_before = bundle_commits;
    reset_after_drain();
    repeat (2) begin
      @(posedge ref_clk);
      #2;
    end
    if (bundle_commits != commits_before ||
        committed_events != events_before_reset ||
        retired_events != retires_before_reset ||
        expected_head != expected_tail || !drain_idle)
      fail("A3_P6_RESET_FAIL", "drained reset created, lost, or replayed work");
    $display("A3_P6_DRAIN_RESET_PASS");

    if (committed_events != retired_events || expected_head != expected_tail ||
        committed_events != 21 || bundle_commits != 11)
      fail("A3_P6_CONSERVATION_FAIL", "final commit/retire totals differ");
    $display("A3_P6_ORDER_CONSERVATION_PASS bundles=%0d events=%0d",
             bundle_commits, committed_events);
    $display("A3_EXACT_SCALAR_PREFIX_K2_P6_ALL_PASS");
    $finish;
  end
endmodule
