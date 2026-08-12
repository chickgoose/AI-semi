`timescale 1ns/1ps

module a2_k2_official_direct_tb;
  logic clk = 1'b0;
  logic rst_n = 1'b0;
  logic [15:0] source_valid = 16'd0;
  logic [15:0] source_ready;
  logic [15:0] source_event [16];
  logic [1:0] retire_ready = 2'b11;
  logic [1:0] retire_valid;
  logic [15:0] retire_event [2];
  logic [3:0] retire_source [2];
  logic drain_idle;

  integer credits [16];
  integer accepted_count = 0;
  integer delivered_count = 0;
  integer source_index;
  integer lane;

  always #5 clk = ~clk;

  a2_k2_official_always_ready_wrapper #(
      .NUM_SOURCES(16),
      .ADDR_WIDTH(16),
      // Directed unit coverage may stop both lanes together.  The common
      // binding fixes this parameter to one and therefore requires 2'b11.
      .OFFICIAL_ALWAYS_READY(1'b0)
  ) dut (
      .clk(clk),
      .rst_n(rst_n),
      .source_valid(source_valid),
      .source_event(source_event),
      .source_ready(source_ready),
      .retire_ready(retire_ready),
      .retire_valid(retire_valid),
      .retire_event(retire_event),
      .retire_source(retire_source),
      .drain_idle(drain_idle)
  );

  task automatic fail(input string reason);
    $fatal(1, "A2_K2_DIRECT_FAIL %s", reason);
  endtask

  task automatic drive_mask(input logic [15:0] mask);
    begin
      @(negedge clk);
      source_valid = mask;
    end
  endtask

  task automatic check_count(input logic [1:0] expected);
    begin
      #1;
      if (dut.core_count !== expected)
        fail($sformatf("count expected=%0d got=%0d", expected,
                       dut.core_count));
      if (retire_valid !== ((expected == 2) ? 2'b11 :
                            (expected == 1) ? 2'b01 : 2'b00))
        fail($sformatf("valid/count mismatch count=%0d valid=%0b",
                       expected, retire_valid));
    end
  endtask

  task automatic commit_then_clear(input logic [1:0] expected_count);
    begin
      check_count(expected_count);
      @(posedge clk);
      @(negedge clk);
      source_valid = 16'd0;
      #1;
    end
  endtask

  always @(posedge clk) begin
    if (!rst_n) begin
      if ((source_ready !== 16'd0) || (retire_valid !== 2'b00))
        fail("reset quiet violation");
      for (source_index = 0; source_index < 16;
           source_index = source_index + 1)
        credits[source_index] = 0;
    end else begin
      // Acceptance is processed before retirement, matching the frozen
      // common scoreboard and allowing a zero-latency normalized beat.
      for (source_index = 0; source_index < 16;
           source_index = source_index + 1) begin
        if (source_valid[source_index] && source_ready[source_index]) begin
          credits[source_index] = credits[source_index] + 1;
          accepted_count = accepted_count + 1;
        end
      end
      for (lane = 0; lane < 2; lane = lane + 1) begin
        if (retire_valid[lane] && retire_ready[lane]) begin
          if (retire_event[lane] !== source_event[retire_source[lane]])
            fail($sformatf("address-only mismatch lane=%0d event=%0h source=%0d",
                           lane, retire_event[lane], retire_source[lane]));
          if (credits[retire_source[lane]] <= 0)
            fail($sformatf("phantom/duplicate lane=%0d source=%0d",
                           lane, retire_source[lane]));
          credits[retire_source[lane]] =
              credits[retire_source[lane]] - 1;
          delivered_count = delivered_count + 1;
        end
      end
    end
  end

  initial begin
    for (source_index = 0; source_index < 16;
         source_index = source_index + 1) begin
      source_event[source_index] = 16'(source_index);
      credits[source_index] = 0;
    end

    repeat (3) @(posedge clk);
    @(negedge clk);
    rst_n = 1'b1;

    // Exact count zero.
    check_count(2'd0);
    if ((source_ready !== 16'd0) || !drain_idle)
      fail("count0 ready/drain mismatch");

    // Exact count one and immediate atomic accept/retire.
    drive_mask(16'h0008);
    commit_then_clear(2'd1);

    // Exact count two.  Both accepted source bits and both normalized lanes
    // must transact on the same edge.
    drive_mask(16'h0012);
    commit_then_clear(2'd2);

    // The common event identity can include an address type bit.  It must be
    // routed from the selected source, never synthesized from private state.
    source_event[6] = 16'ha5c6;
    drive_mask(16'h0040);
    check_count(2'd1);
    if ((retire_source[0] !== 4'd6) || (retire_event[0] !== 16'ha5c6))
      fail("selected event identity was not routed exactly");
    commit_then_clear(2'd1);

    // Uniform stop/refill is only a directed atomicity probe.  It does not
    // advertise independent-lane backpressure capability.
    @(negedge clk);
    retire_ready = 2'b00;
    source_valid = 16'h0300;
    check_count(2'd2);
    if (source_ready !== 16'd0)
      fail("credit visible while uniformly stalled");
    begin : hold_check
      logic [15:0] held_event0;
      logic [15:0] held_event1;
      held_event0 = retire_event[0];
      held_event1 = retire_event[1];
      repeat (2) begin
        @(posedge clk);
        #1;
        if ((retire_valid !== 2'b11) ||
            (retire_event[0] !== held_event0) ||
            (retire_event[1] !== held_event1) ||
            (source_ready !== 16'd0))
          fail("atomic uniform-stall hold changed");
      end
    end
    @(negedge clk);
    retire_ready = 2'b11;
    #1;
    if (source_ready !== dut.core_bitmap)
      fail("atomic refill did not expose both credits");
    @(posedge clk);
    @(negedge clk);
    source_valid = 16'd0;
    #1;

    if (!drain_idle || (retire_valid !== 2'b00))
      fail("pre-reset drain guard failed");
    if (accepted_count != delivered_count)
      fail($sformatf("pre-reset conservation accepted=%0d delivered=%0d",
                     accepted_count, delivered_count));

    // Reset is asserted only after complete drain.  No mid-traffic flush or
    // preserve behavior is imposed.
    @(negedge clk);
    rst_n = 1'b0;
    repeat (3) @(posedge clk);
    @(negedge clk);
    if ((source_ready !== 16'd0) || (retire_valid !== 2'b00))
      fail("reset output not quiet");
    rst_n = 1'b1;

    // Post-reset addresses are disjoint from the pre-reset directed set.
    drive_mask(16'hc000);
    commit_then_clear(2'd2);
    if (!drain_idle || (retire_valid !== 2'b00))
      fail("post-reset drain guard failed");
    if (accepted_count != delivered_count)
      fail($sformatf("final conservation accepted=%0d delivered=%0d",
                     accepted_count, delivered_count));
    for (source_index = 0; source_index < 16;
         source_index = source_index + 1) begin
      if (credits[source_index] != 0)
        fail($sformatf("credit leak source=%0d count=%0d",
                       source_index, credits[source_index]));
    end

    $display("A2_K2_DIRECT_PASS accepted=%0d delivered=%0d",
             accepted_count, delivered_count);
    $finish;
  end
endmodule
