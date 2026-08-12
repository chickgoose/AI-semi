`timescale 1ns/1ps

module a4_paired_cortical_column_k2_p6_tb;
  logic ref_clk = 1'b0;
  logic sample_clk = 1'b0;
  logic rst_n = 1'b0;
  logic link_enable = 1'b1;
  logic [15:0] source_pending = 16'b0;

  logic [15:0] source_ready;
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
  integer committed_bundles = 0;
  integer committed_events = 0;
  integer retired_bundles = 0;
  integer retired_events = 0;
  integer p6_frames = 0;
  integer expected_head = 0;
  integer expected_tail = 0;
  integer expected_count [0:255];
  integer expected_addr0 [0:255];
  integer expected_addr1 [0:255];
  integer row_count [0:3];

  logic sampled_ready = 1'b0;
  logic sampled_commit = 1'b0;
  logic [1:0] sampled_count = 2'd0;
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

  a4_paired_cortical_column_k2_p6_top dut (
    .ref_clk_i(ref_clk),
    .sample_clk_i(sample_clk),
    .rst_n,
    .link_enable_i(link_enable),
    .source_pending_i(source_pending),
    .source_ready_o(source_ready),
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
      $display("%s cycle=%0d reason=%s", marker, cycle, reason);
      $fatal(1, "%s", reason);
    end
  endtask

  function automatic integer popcount16(input logic [15:0] value);
    integer index;
    begin
      popcount16 = 0;
      for (index = 0; index < 16; index = index + 1)
        popcount16 = popcount16 + value[index];
    end
  endfunction

  always @(posedge p6_clk) begin
    if (rst_n)
      p6_frames = p6_frames + 1;
  end

  // Public-boundary end-to-end scoreboard.  It treats the two retire lanes as
  // one record and never infers success from internal endpoint state.
  always @(posedge ref_clk) begin
    cycle = cycle + 1;
    sampled_ready = bundle_ready;
    sampled_commit = bundle_commit;
    sampled_count = grant_count;

    if (!rst_n) begin
      prior_stalled = 1'b0;
    end else begin
      if (bundle_valid !== (grant_count != 2'd0) || grant_count > 2)
        fail("A4_P6_COUNT_FAIL", "invalid K2 valid/count encoding");
      if ((grant_count == 2) && (grant_addr0 == grant_addr1))
        fail("A4_P6_ORDER_FAIL", "owner emitted a duplicate pair");
      if (bundle_protocol_error || retire_protocol_error)
        fail("A4_P6_PROTOCOL_FAIL", "integrated protocol error");
      if (bundle_commit !== (bundle_valid && bundle_ready))
        fail("A4_P6_ATOMIC_FAIL", "commit diverged from whole-bundle fire");
      if (policy_microsteps !==
          (bundle_commit ? grant_count : 2'd0))
        fail("A4_P6_PROTOCOL_FAIL", "policy microsteps differ from bundle");
      if (popcount16(source_ready) !=
          (bundle_commit ? grant_count : 0))
        fail("A4_P6_ATOMIC_FAIL", "source acknowledgement was partial");
      if ((source_ready & ~source_pending) != 0)
        fail("A4_P6_ATOMIC_FAIL", "acknowledged a non-pending source");

      if (prior_stalled &&
          (!bundle_valid || grant_count !== stalled_count ||
           grant_addr0 !== stalled_addr0 ||
           grant_addr1 !== stalled_addr1))
        fail("A4_P6_STALL_FAIL", "atomic offer changed while stalled");
      prior_stalled = bundle_valid && !bundle_ready;
      stalled_count = grant_count;
      stalled_addr0 = grant_addr0;
      stalled_addr1 = grant_addr1;

      if (bundle_commit) begin
        expected_count[expected_tail] = grant_count;
        expected_addr0[expected_tail] = grant_addr0;
        expected_addr1[expected_tail] =
            (grant_count == 2) ? grant_addr1 : 0;
        expected_tail = expected_tail + 1;
        committed_bundles = committed_bundles + 1;
        committed_events = committed_events + grant_count;

        if (continuous_mode) begin
          if ((previous_continuous_cycle >= 0) &&
              (cycle != previous_continuous_cycle + 1))
            fail("A4_P6_CONTINUOUS_FAIL", "continuous commit bubble");
          if (grant_count != 2)
            fail("A4_P6_CONTINUOUS_FAIL", "persistent demand missed K2");
          row_count[grant_addr0[3:2]] =
              row_count[grant_addr0[3:2]] + 1;
          row_count[grant_addr1[3:2]] =
              row_count[grant_addr1[3:2]] + 1;
          previous_continuous_cycle = cycle;
          continuous_commits = continuous_commits + 1;
        end
      end
    end

    #1;
    if (!rst_n) begin
      if (grant_count != 0 || bundle_valid || bundle_commit ||
          source_ready != 0 || policy_microsteps != 0 ||
          retire_valid != 0 || p6_clk)
        fail("A4_P6_RESET_FAIL", "live work escaped reset");
    end else if (retire_valid != 0) begin
      if (expected_head == expected_tail)
        fail("A4_P6_CONSERVATION_FAIL", "retirement without commit");
      if (retire_valid !== ((expected_count[expected_head] == 2) ?
                            2'b11 : 2'b01) ||
          retire_addr0 !== expected_addr0[expected_head][3:0] ||
          retire_addr1 !== expected_addr1[expected_head][3:0])
        fail("A4_P6_ORDER_FAIL", "P6 changed bundle shape or order");
      retired_events = retired_events + expected_count[expected_head];
      retired_bundles = retired_bundles + 1;
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
        if (timeout > 16)
          fail("A4_P6_DRAIN_FAIL", "integrated drain timed out");
      end
      if (bundle_valid || p6_clk || retire_valid)
        fail("A4_P6_DRAIN_FAIL", "drain asserted with visible work");
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
      @(negedge ref_clk);
      rst_n = 1'b1;
      @(posedge ref_clk);
      #2;
      if (sampled_ready || sampled_commit || grant_count || retire_valid)
        fail("A4_P6_RESET_FAIL", "reset-arm edge was not quiet");
    end
  endtask

  initial begin
    integer phase_before;
    integer token_before;
    integer frames_before;
    integer commits_before;
    integer events_before_reset;
    integer retires_before_reset;
    logic [1:0] held_count;
    logic [3:0] held_addr0;
    logic [3:0] held_addr1;

    // Live request pins during reset must remain completely quiet.
    source_pending = 16'hffff;
    repeat (3) @(posedge ref_clk);
    #2;
    if (!drain_idle)
      fail("A4_P6_RESET_FAIL", "reset state was not drained");
    @(negedge ref_clk);
    source_pending = 16'b0;
    rst_n = 1'b1;
    @(posedge ref_clk);
    #2;
    if (sampled_ready || bundle_valid || bundle_commit || grant_count ||
        policy_microsteps || retire_valid)
      fail("A4_P6_COUNT0_FAIL", "count-zero arm cycle was not empty");
    @(posedge ref_clk);
    #2;
    if (bundle_valid || bundle_commit || grant_count || p6_frames)
      fail("A4_P6_COUNT0_FAIL", "empty request launched traffic");
    $display("A4_P6_COUNT0_RESET_PASS");

    reset_after_drain();
    drive(16'h0020, 1'b1);
    @(posedge ref_clk);
    #2;
    if (!sampled_commit || sampled_count != 1 || grant_addr0 != 4'd5)
      fail("A4_P6_COUNT1_FAIL", "singleton did not commit exactly once");
    drive(16'h0000, 1'b1);
    wait_drained();
    $display("A4_P6_COUNT1_PASS");

    reset_after_drain();
    drive(16'h1001, 1'b1);
    @(posedge ref_clk);
    #2;
    if (!sampled_commit || sampled_count != 2 ||
        grant_addr0 == grant_addr1)
      fail("A4_P6_COUNT2_FAIL", "pair did not commit atomically");
    drive(16'h0000, 1'b1);
    wait_drained();
    $display("A4_P6_COUNT2_PASS");

    // Queue-free admission stall.  Snapshot both the public ordered bundle
    // and A4's native policy state; neither may move before atomic release.
    reset_after_drain();
    drive(16'h1001, 1'b0);
    @(posedge ref_clk);
    #2;
    if (grant_count != 2 || bundle_ready || bundle_commit)
      fail("A4_P6_STALL_FAIL", "known pair did not enter stall");
    held_count = grant_count;
    held_addr0 = grant_addr0;
    held_addr1 = grant_addr1;
    phase_before = dut.scheduler.phase_q;
    token_before = dut.scheduler.token_q;
    frames_before = p6_frames;
    source_pending = 16'hf11f;
    repeat (4) begin
      @(posedge ref_clk);
      #2;
      if (bundle_ready || bundle_commit || policy_microsteps || p6_clk ||
          drain_idle || grant_count !== held_count ||
          grant_addr0 !== held_addr0 || grant_addr1 !== held_addr1 ||
          dut.scheduler.phase_q != phase_before ||
          dut.scheduler.token_q != token_before ||
          p6_frames != frames_before)
        fail("A4_P6_STALL_FAIL", "offer, policy, or P6 moved in stall");
    end
    drive(16'h1001, 1'b1);
    @(posedge ref_clk);
    #2;
    if (!sampled_commit || sampled_count != 2)
      fail("A4_P6_STALL_FAIL", "held pair did not release atomically");
    drive(16'h0000, 1'b1);
    wait_drained();
    $display("A4_P6_STALL_PASS");

    // Exactly one native six-phase epoch: five (row1,row2) pairs followed by
    // one (row0,row3) pair.  This checks A4's aggregate policy without
    // substituting any scalar/flattened wheel.
    reset_after_drain();
    row_count[0] = 0;
    row_count[1] = 0;
    row_count[2] = 0;
    row_count[3] = 0;
    continuous_commits = 0;
    previous_continuous_cycle = -1;
    continuous_mode = 1'b1;
    drive(16'hffff, 1'b1);
    repeat (6) begin
      @(posedge ref_clk);
      #2;
    end
    drive(16'h0000, 1'b1);
    continuous_mode = 1'b0;
    if (continuous_commits != 6 || row_count[0] != 1 ||
        row_count[1] != 5 || row_count[2] != 5 || row_count[3] != 1)
      fail("A4_P6_CONTINUOUS_FAIL", "native [1,5,5,1] epoch changed");
    wait_drained();
    $display("A4_P6_CONTINUOUS_PASS rows=%0d,%0d,%0d,%0d",
             row_count[0], row_count[1], row_count[2], row_count[3]);

    events_before_reset = committed_events;
    retires_before_reset = retired_events;
    commits_before = committed_bundles;
    reset_after_drain();
    repeat (2) begin
      @(posedge ref_clk);
      #2;
    end
    if (committed_bundles != commits_before ||
        committed_events != events_before_reset ||
        retired_events != retires_before_reset ||
        expected_head != expected_tail || !drain_idle)
      fail("A4_P6_RESET_FAIL", "drained reset replayed or lost work");
    $display("A4_P6_DRAIN_RESET_PASS");

    if (committed_events != retired_events ||
        committed_bundles != retired_bundles ||
        expected_head != expected_tail || committed_events != 17 ||
        committed_bundles != 9)
      fail("A4_P6_CONSERVATION_FAIL", "commit/retire totals differ");
    $display("A4_P6_ORDER_CONSERVATION_PASS bundles=%0d events=%0d seam_state_bits=0",
             committed_bundles, committed_events);
    $display("A4_PAIRED_CORTICAL_COLUMN_K2_P6_ALL_PASS");
    $finish;
  end
endmodule
