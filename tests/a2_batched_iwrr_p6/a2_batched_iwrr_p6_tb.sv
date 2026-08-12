`timescale 1ns/1ps

module a2_batched_iwrr_p6_tb;
  logic ref_clk = 1'b0;
  logic sample_clk = 1'b0;
  logic rst_n = 1'b0;
  logic link_enable = 1'b1;
  logic [15:0] req = 16'd0;

  logic grant_commit;
  logic [1:0] grant_count;
  logic [3:0] grant_addr0, grant_addr1;
  logic [15:0] grant_bitmap;
  logic p6_clk;
  logic [4:0] p6_data;
  logic [1:0] retire_valid;
  logic [3:0] retire_addr0, retire_addr1;
  logic protocol_error;
  logic drain_idle;

  integer cycle = 0;
  integer accepted_bundles = 0;
  integer accepted_events = 0;
  integer retired_bundles = 0;
  integer retired_events = 0;
  integer p6_frames = 0;
  integer expected_head = 0;
  integer expected_tail = 0;
  integer expected_count [0:255];
  integer expected_addr0 [0:255];
  integer expected_addr1 [0:255];
  integer row_count [0:3];
  integer token_before;
  integer frames_before;
  integer bundles_before;
  logic [1:0] stalled_count;
  logic [3:0] stalled_addr0, stalled_addr1;
  logic [15:0] stalled_bitmap;

  always #8 ref_clk = ~ref_clk;
  initial begin
    #4;
    forever #8 sample_clk = ~sample_clk;
  end

  a2_batched_iwrr_p6_top dut (
    .ref_clk_i(ref_clk),
    .sample_clk_i(sample_clk),
    .rst_n,
    .link_enable_i(link_enable),
    .req_i(req),
    .grant_commit_o(grant_commit),
    .grant_count_o(grant_count),
    .grant_addr0_o(grant_addr0),
    .grant_addr1_o(grant_addr1),
    .grant_bitmap_o(grant_bitmap),
    .p6_clk_o(p6_clk),
    .p6_data_o(p6_data),
    .retire_valid_o(retire_valid),
    .retire_addr0_o(retire_addr0),
    .retire_addr1_o(retire_addr1),
    .protocol_error_o(protocol_error),
    .drain_idle_o(drain_idle)
  );

  task automatic fail(input string marker, input string reason);
    begin
      $display("%s cycle=%0d reason=%s", marker, cycle, reason);
      $fatal(1, "%s", reason);
    end
  endtask

  function automatic integer bitmap_popcount(input logic [15:0] value);
    integer index;
    begin
      bitmap_popcount = 0;
      for (index = 0; index < 16; index = index + 1)
        bitmap_popcount = bitmap_popcount + value[index];
    end
  endfunction

  always @(posedge p6_clk) begin
    if (rst_n)
      p6_frames = p6_frames + 1;
  end

  always @(posedge ref_clk) begin
    cycle = cycle + 1;

    if (rst_n && grant_commit) begin
      if ((grant_count != 2'd1) && (grant_count != 2'd2))
        fail("A2_P6_COUNT_FAIL", "committed count was outside one/two");
      if (bitmap_popcount(grant_bitmap) != grant_count)
        fail("A2_P6_CONSERVATION_FAIL", "grant bitmap/count mismatch");
      if (!grant_bitmap[grant_addr0] ||
          ((grant_count == 2'd2) &&
           (!grant_bitmap[grant_addr1] || (grant_addr0 == grant_addr1))))
        fail("A2_P6_ORDER_FAIL", "grant addresses do not match atomic bitmap");
      expected_count[expected_tail] = grant_count;
      expected_addr0[expected_tail] = grant_addr0;
      expected_addr1[expected_tail] = (grant_count == 2'd2) ?
                                      grant_addr1 : 0;
      expected_tail = expected_tail + 1;
      accepted_bundles = accepted_bundles + 1;
      accepted_events = accepted_events + grant_count;
    end

    #1;
    if (rst_n && protocol_error)
      fail("A2_P6_PROTOCOL_FAIL", "integrated endpoint reported an error");
    if (!rst_n && (grant_commit || (grant_count != 0) ||
                   (retire_valid != 0) || p6_clk))
      fail("A2_P6_RESET_FAIL", "activity escaped while reset was active");
    if (rst_n && (retire_valid != 2'b00)) begin
      if (expected_head == expected_tail)
        fail("A2_P6_CONSERVATION_FAIL", "retirement without acceptance");
      if (retire_valid !== ((expected_count[expected_head] == 2) ?
                            2'b11 : 2'b01) ||
          retire_addr0 !== expected_addr0[expected_head][3:0] ||
          retire_addr1 !== expected_addr1[expected_head][3:0])
        fail("A2_P6_ORDER_FAIL", "retirement was partial or reordered");
      retired_events = retired_events + expected_count[expected_head];
      retired_bundles = retired_bundles + 1;
      expected_head = expected_head + 1;
    end
  end

  task automatic drive_req(input logic [15:0] value);
    begin
      @(negedge ref_clk);
      req = value;
    end
  endtask

  task automatic wait_for_commit;
    begin
      while (!grant_commit)
        @(posedge ref_clk);
    end
  endtask

  task automatic wait_for_drain;
    integer timeout;
    begin
      drive_req(16'd0);
      timeout = 0;
      while ((!drain_idle || (expected_head != expected_tail)) &&
             (timeout < 20)) begin
        @(negedge ref_clk);
        timeout = timeout + 1;
      end
      if (!drain_idle || (expected_head != expected_tail))
        fail("A2_P6_DRAIN_FAIL", "accepted traffic did not drain");
    end
  endtask

  initial begin
    repeat (3) @(posedge ref_clk);
    if (!drain_idle)
      fail("A2_P6_RESET_FAIL", "empty reset state did not report drain");

    // Reset release is charged: the P6 endpoint arms for one reference edge.
    // The explicit buffer may accept during that edge, but no P6 frame may.
    @(negedge ref_clk);
    rst_n = 1'b1;
    req = 16'h0110;
    @(posedge ref_clk);
    #1;
    if (!grant_commit || !dut.buffer_valid_q || (p6_frames != 0))
      fail("A2_P6_STALL_FAIL", "charged arm buffering failed");
    drive_req(16'd0);
    wait_for_drain();
    $display("A2_P6_RESET_PASS");

    // A count-zero offer is represented by no valid bundle.  It consumes no
    // calendar state and launches no physical frame.
    token_before = dut.scheduler.token_cursor_q;
    frames_before = p6_frames;
    repeat (4) @(posedge ref_clk);
    if (grant_commit || (grant_count != 0) ||
        (dut.scheduler.token_cursor_q != token_before) ||
        (p6_frames != frames_before))
      fail("A2_P6_COUNT0_FAIL", "count-zero changed policy or link state");
    $display("A2_P6_COUNT0_PASS");

    // Singleton and pair bundles exercise both legal K2 shapes.
    drive_req(16'h0008);
    wait_for_commit();
    if ((grant_count != 1) || (grant_addr0 != 3) ||
        (grant_bitmap != 16'h0008))
      fail("A2_P6_COUNT1_FAIL", "singleton grant shape mismatch");
    drive_req(16'd0);
    wait_for_drain();
    $display("A2_P6_COUNT1_PASS");

    drive_req(16'h8001);
    wait_for_commit();
    if ((grant_count != 2) || (grant_bitmap != 16'h8001))
      fail("A2_P6_COUNT2_FAIL", "pair grant shape mismatch");
    drive_req(16'd0);
    wait_for_drain();
    $display("A2_P6_COUNT2_PASS");

    // Disable link admission after filling the charged buffer.  A second A2
    // offer must remain stable and uncommitted until the complete first bundle
    // can leave; no per-lane progress is observable.
    @(negedge ref_clk);
    link_enable = 1'b0;
    req = 16'h0110;
    frames_before = p6_frames;
    bundles_before = accepted_bundles;
    @(posedge ref_clk);
    #1;
    if ((accepted_bundles != bundles_before + 1) || !dut.buffer_valid_q)
      fail("A2_P6_STALL_FAIL", "empty buffer did not accept atomically");
    drive_req(16'h1001);
    @(posedge ref_clk);
    #1;
    stalled_count = grant_count;
    stalled_addr0 = grant_addr0;
    stalled_addr1 = grant_addr1;
    stalled_bitmap = grant_bitmap;
    if (grant_commit || (stalled_count == 0))
      fail("A2_P6_STALL_FAIL", "full buffer did not stall scheduler");
    repeat (3) begin
      @(posedge ref_clk);
      #1;
      if (grant_commit || (grant_count !== stalled_count) ||
          (grant_addr0 !== stalled_addr0) ||
          (grant_addr1 !== stalled_addr1) ||
          (grant_bitmap !== stalled_bitmap) ||
          (p6_frames != frames_before))
        fail("A2_P6_STALL_FAIL", "atomic offer changed during stall");
    end
    @(negedge ref_clk);
    link_enable = 1'b1;
    @(posedge ref_clk);
    if (!grant_commit)
      fail("A2_P6_STALL_FAIL", "held offer did not commit on release");
    drive_req(16'd0);
    wait_for_drain();
    $display("A2_P6_STALL_PASS");

    // Six continuous K2 commits consume one complete 12-token IWRR calendar.
    // The elastic register must dequeue/refill on every edge without bubbles.
    row_count[0] = 0;
    row_count[1] = 0;
    row_count[2] = 0;
    row_count[3] = 0;
    drive_req(16'hffff);
    for (integer bundle = 0; bundle < 6; bundle = bundle + 1) begin
      @(posedge ref_clk);
      if (!grant_commit || (grant_count != 2))
        fail("A2_P6_CONTINUOUS_FAIL", "continuous K2 bundle bubble");
      row_count[grant_addr0[3:2]] = row_count[grant_addr0[3:2]] + 1;
      row_count[grant_addr1[3:2]] = row_count[grant_addr1[3:2]] + 1;
    end
    drive_req(16'd0);
    wait_for_drain();
    if ((row_count[0] != 1) || (row_count[1] != 5) ||
        (row_count[2] != 5) || (row_count[3] != 1))
      fail("A2_P6_CONTINUOUS_FAIL", "IWRR calendar service ratio changed");
    $display("A2_P6_CONTINUOUS_PASS rows=%0d,%0d,%0d,%0d",
             row_count[0], row_count[1], row_count[2], row_count[3]);

    // Legal drain/reset/rearm clears scheduler, buffer, and endpoint state.
    @(negedge ref_clk);
    rst_n = 1'b0;
    req = 16'hffff;
    repeat (2) @(posedge ref_clk);
    #1;
    if (dut.buffer_valid_q || (dut.scheduler.token_cursor_q != 0) ||
        (dut.scheduler.hold_q != 0))
      fail("A2_P6_RESET_FAIL", "integration state survived reset");
    @(negedge ref_clk);
    req = 16'd0;
    rst_n = 1'b1;
    repeat (2) @(posedge ref_clk);
    wait_for_drain();

    if ((accepted_events != retired_events) ||
        (accepted_bundles != retired_bundles) ||
        (expected_head != expected_tail) || protocol_error)
      fail("A2_P6_CONSERVATION_FAIL", "accept/retire totals differ");
    $display("A2_P6_ORDER_PASS bundles=%0d", retired_bundles);
    $display("A2_P6_DRAIN_PASS");
    $display("A2_P6_CONSERVATION_PASS accepted=%0d retired=%0d buffer_state_bits=11",
             accepted_events, retired_events);
    $display("A2_P6_ALL_PASS");
    $finish;
  end
endmodule
