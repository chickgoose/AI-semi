`timescale 1ns/1ps

module a4_k2_common_ordering_tb;
  logic clk = 1'b0;
  always #5 clk = ~clk;

  logic rst;
  logic [15:0] source_valid;
  logic [31:0] source_event [16];
  wire [15:0] source_ready;
  wire [1:0] accept_valid;
  wire [3:0] accept_source [2];
  wire [31:0] accept_event [2];
  wire [1:0] retire_valid;
  logic [1:0] retire_ready;
  wire [3:0] retire_source [2];
  wire [31:0] retire_event [2];
  wire drain_idle;

  logic [15:0] pending_valid;
  logic [31:0] pending_event [16];
  integer accepted_ids [0:3];
  integer accepted_sources [0:3];
  integer accepted_head;
  integer accepted_tail;
  integer generated_count;
  integer overrun_count;
  integer accepted_count;
  integer retired_count;
  integer source;
  integer lane;
  string owner_name;

  assign source_valid = pending_valid;
  always @* begin
    for (source = 0; source < 16; source = source + 1)
      source_event[source] = pending_event[source];
  end

  a4_k2_transaction_boundary dut (
    .clk(clk), .rst(rst),
    .source_valid(source_valid), .source_event(source_event),
    .source_ready(source_ready),
    .accept_valid(accept_valid), .accept_source(accept_source),
    .accept_event(accept_event),
    .retire_valid(retire_valid), .retire_ready(retire_ready),
    .retire_source(retire_source), .retire_event(retire_event),
    .drain_idle(drain_idle)
  );

  task automatic fail(input string reason);
    begin
      $display("A4_K2_COMMON_ORDERING_FAIL owner=%s reason=%s", owner_name, reason);
      $fatal(1, "A4_K2_COMMON_ORDERING_FAIL %s", reason);
    end
  endtask

  // Called only at negedge, matching offer_event_record in the frozen common
  // TB.  Pending is tested before the following posedge handshake.
  task automatic common_occurrence(input integer source_index,
                                   input integer event_code);
    begin
      generated_count = generated_count + 1;
      if (pending_valid[source_index]) begin
        overrun_count = overrun_count + 1;
      end else begin
        pending_valid[source_index] = 1'b1;
        pending_event[source_index] = event_code;
      end
    end
  endtask

  // Common scoreboard order is source acceptance first, then retirement.
  always @(posedge clk) begin
    if (!rst) begin
      for (lane = 0; lane < 2; lane = lane + 1) begin
        if (accept_valid[lane]) begin
          source = accept_source[lane];
          if (!pending_valid[source] || accept_event[lane] != pending_event[source])
            fail("phantom/corrupt acceptance");
          accepted_ids[accepted_tail] = accept_event[lane];
          accepted_sources[accepted_tail] = source;
          accepted_tail = accepted_tail + 1;
          accepted_count = accepted_count + 1;
          pending_valid[source] <= 1'b0;
        end
      end
      for (lane = 0; lane < 2; lane = lane + 1) begin
        if (retire_valid[lane] && retire_ready[lane]) begin
          if (accepted_head >= accepted_tail)
            fail("retirement preceded acceptance");
          if (retire_event[lane] != accepted_ids[accepted_head] ||
              retire_source[lane] != accepted_sources[accepted_head])
            fail("retirement global order/identity mismatch");
          accepted_head = accepted_head + 1;
          retired_count = retired_count + 1;
        end
      end
    end
  end

  initial begin
    integer timeout;
    if (!$value$plusargs("OWNER=%s", owner_name))
      owner_name = "unknown";
    rst = 1'b1;
    retire_ready = 2'b00;
    pending_valid = '0;
    generated_count = 0;
    overrun_count = 0;
    accepted_count = 0;
    retired_count = 0;
    accepted_head = 0;
    accepted_tail = 0;
    for (source = 0; source < 16; source = source + 1)
      pending_event[source] = '0;

    repeat (2) @(posedge clk);
    @(negedge clk);
    rst = 1'b0;
    common_occurrence(4, 1); // old record
    @(posedge clk); #1;
    if (accepted_count != 0 || retired_count != 0)
      fail("old record escaped atomic output stall");

    // P0 witness: the new occurrence is classified while source 4 is still
    // pending, immediately before the edge that accepts/retires event 1.
    @(negedge clk);
    retire_ready = 2'b11;
    common_occurrence(4, 2);
    if (overrun_count != 1)
      fail("same-edge retrigger was not classified as overrun");
    @(posedge clk); #1;
    if (accepted_count != 1 || retired_count != 1 || accepted_ids[0] != 1)
      fail("old record did not fire after new occurrence classification");

    // Rearm is visible at the next occurrence edge, never retroactively.
    @(negedge clk);
    common_occurrence(4, 3);
    if (overrun_count != 1)
      fail("post-fire rearm remained occupied");
    timeout = 0;
    while ((accepted_count != 2 || retired_count != 2) && timeout < 8) begin
      @(posedge clk); #1;
      timeout = timeout + 1;
    end
    if (timeout >= 8 || accepted_ids[1] != 3)
      fail("post-fire sentinel did not accept/retire");

    @(negedge clk);
    repeat (2) @(posedge clk);
    #1;
    if (generated_count != 3 || overrun_count != 1 ||
        accepted_count != 2 || retired_count != 2 ||
        generated_count != overrun_count + accepted_count ||
        accepted_head != accepted_tail || !drain_idle)
      fail("final common-ordering conservation/drain mismatch");
    $display("A4_K2_COMMON_ORDERING_PASS owner=%s generated=3 overrun=1 accepted=2 retired=2",
             owner_name);
    $finish;
  end
endmodule
