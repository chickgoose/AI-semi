`timescale 1ns/1ps
/* verilator lint_off DECLFILENAME */
/* verilator lint_off BLKSEQ */

module a4_pcck2_ordered_link_tb;
  logic clk = 0;
  logic rst_n = 0;
  logic [15:0] source_valid = 0;
  logic [15:0] source_ready;
  logic [1:0] retire_valid;
  logic [1:0] retire_ready = 0;
  logic [7:0] retire_addr;
  logic drain_idle;

  integer accepted_count;
  integer retired_count;
  integer expected_head;
  integer expected_tail;
  logic [3:0] expected_addr [0:31];
  integer lane;

  a4_pcck2_ordered_link_adapter dut (.*);
  always #5 clk <= ~clk;

  task automatic fail(input string reason);
    begin
      $display("A4_PCCK2_ORDERED_LINK_FAIL reason=%s", reason);
      $fatal(1, "A4_PCCK2_ORDERED_LINK_FAIL %s", reason);
    end
  endtask

  task automatic drive(input logic [1:0] ready,
                       input logic [15:0] new_sources);
    begin
      @(negedge clk);
      retire_ready = ready;
      if ((source_valid & new_sources) != 0)
        fail("test attempted duplicate pending source");
      source_valid = source_valid | new_sources;
      @(posedge clk);
      #1;
    end
  endtask

  // Same-edge ordering matches the common scoreboard: accepted scheduler
  // entries append first, then visible retire handshakes consume the global
  // ordered prefix.  The link itself is the only state under test.
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      accepted_count = 0;
      retired_count = 0;
      expected_head = 0;
      expected_tail = 0;
    end else begin
      if (retire_valid[1] && retire_ready[1] &&
          !(retire_valid[0] && retire_ready[0]))
        fail("younger lane bypassed stalled head");

      if (dut.scheduler_bundle_ready && (dut.scheduler_grant_count != 0)) begin
        expected_addr[expected_tail] = dut.scheduler_grant_addr[3:0];
        expected_tail = expected_tail + 1;
        accepted_count = accepted_count + 1;
        if (dut.scheduler_grant_count == 2) begin
          expected_addr[expected_tail] = dut.scheduler_grant_addr[7:4];
          expected_tail = expected_tail + 1;
          accepted_count = accepted_count + 1;
        end
      end

      for (lane = 0; lane < 2; lane = lane + 1) begin
        if (retire_valid[lane] && retire_ready[lane]) begin
          if (expected_head >= expected_tail)
            fail("phantom retirement");
          if (retire_addr[lane*4 +: 4] !== expected_addr[expected_head][3:0])
            fail("global retirement order mismatch");
          expected_head = expected_head + 1;
          retired_count = retired_count + 1;
        end
      end
      source_valid <= source_valid & ~source_ready;
      if (retired_count > accepted_count)
        fail("retired count exceeds accepted count");
    end
  end

  initial begin
    logic [3:0] old_head;
    logic [3:0] old_younger;
    logic [3:0] refill;

    repeat (2) @(posedge clk);
    @(negedge clk);
    rst_n = 1;

    // Fill exactly two entries while the external link is stopped.
    source_valid = 16'h0110; // row1/source4 plus row2/source8
    retire_ready = 2'b00;
    @(posedge clk); #1;
    if (dut.queue_count_q != 2 || accepted_count != 2)
      fail("initial atomic pair did not fill link");
    old_head = dut.queue_addr_q[0];
    old_younger = dut.queue_addr_q[1];
    if (retire_valid != 2'b01)
      fail("blocked head must hide younger lane");

    // ready=10 is the original correctness counterexample: no handshake,
    // no queue movement, and no scheduler refill are permitted.
    drive(2'b10, 16'h0000);
    if (retire_valid != 2'b01 || dut.queue_count_q != 2 ||
        dut.queue_addr_q[0] != old_head || dut.queue_addr_q[1] != old_younger)
      fail("ready10 changed the ordered pair");
    if (retired_count != 0 || accepted_count != 2 || source_ready != 0)
      fail("ready10 violated order/conservation");
    $display("A4_PCCK2_ORDERED_LINK_READY10_PASS");

    // ready=01 retires only the head.  One new scalar scheduler commit fills
    // the freed tail on the same edge: old younger compacts to q0, refill q1.
    drive(2'b01, 16'h0001);
    refill = dut.queue_addr_q[1];
    if (dut.queue_count_q != 2 || dut.queue_addr_q[0] != old_younger ||
        refill != 0 || accepted_count != 3 || retired_count != 1)
      fail("ready01 compaction/refill mismatch");
    $display("A4_PCCK2_ORDERED_LINK_READY01_COMPACT_REFILL_PASS");

    // The refilled pair is still protected against a younger-only ready.
    drive(2'b10, 16'h0000);
    if (dut.queue_count_q != 2 || retired_count != 1)
      fail("refilled younger bypassed on ready10");

    // Retire both old entries and atomically refill a fresh pair on the same
    // edge, then drain it in order.
    drive(2'b11, 16'h0220); // one source in each center row
    if (dut.queue_count_q != 2 || accepted_count != 5 || retired_count != 3)
      fail("full retire/refill conservation mismatch");
    $display("A4_PCCK2_ORDERED_LINK_FULL_REFILL_PASS");

    drive(2'b11, 16'h0000);
    if (dut.queue_count_q != 0 || expected_head != expected_tail ||
        accepted_count != retired_count || accepted_count != 5)
      fail("final order/conservation mismatch");
    drive(2'b11, 16'h0000);
    if (!drain_idle || retire_valid != 0)
      fail("adapter did not reach truthful drain idle");
    $display("A4_PCCK2_ORDERED_LINK_ORDER_CONSERVATION_PASS accepted=%0d retired=%0d",
             accepted_count, retired_count);
    $display("A4_PCCK2_ORDERED_LINK_PASS");
    $finish;
  end
endmodule
