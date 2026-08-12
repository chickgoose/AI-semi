`timescale 1ns/1ps

module a2_batched_iwrr_k2_normalized_tb;
  localparam int NUM_SOURCES = 16;
  localparam int ADDR_WIDTH = 16;
  localparam int RETIRE_LANES = 2;

  logic clk = 0;
  logic rst_n = 0;
  logic [15:0] source_valid = 0;
  logic [15:0] source_ready;
  logic [15:0] source_event [16];
  logic [255:0] source_event_packed;
  logic [1:0] retire_valid;
  logic [1:0] retire_ready = 0;
  logic [15:0] retire_event [2];
  logic [31:0] retire_event_packed;
  logic [3:0] retire_source [2];
  logic [7:0] retire_source_packed;
  logic drain_idle;

  integer accepted_total = 0;
  integer retired_total = 0;
  integer source;
  logic [3:0] held_addr0, held_addr1;

  genvar view_index;
  generate
    for (view_index = 0; view_index < 16; view_index = view_index + 1) begin : source_view
      assign source_event_packed[view_index*16 +: 16] = source_event[view_index];
    end
    for (view_index = 0; view_index < 2; view_index = view_index + 1) begin : retire_view
      assign retire_event[view_index] = retire_event_packed[view_index*16 +: 16];
      assign retire_source[view_index] = retire_source_packed[view_index*4 +: 4];
    end
  endgenerate

  a2_batched_iwrr_k2_normalized dut (
    .clk(clk),
    .rst_n(rst_n),
    .source_valid(source_valid),
    .source_ready(source_ready),
    .source_event(source_event_packed),
    .retire_valid(retire_valid),
    .retire_ready(retire_ready),
    .retire_event(retire_event_packed),
    .retire_source(retire_source_packed),
    .drain_idle(drain_idle)
  );
  always #5 clk = ~clk;

  function automatic integer popcount16(input logic [15:0] value);
    integer bit_index;
    begin
      popcount16 = 0;
      for (bit_index = 0; bit_index < 16; bit_index = bit_index + 1)
        popcount16 = popcount16 + value[bit_index];
    end
  endfunction

  function automatic integer popcount2(input logic [1:0] value);
    begin
      popcount2 = value[0] + value[1];
    end
  endfunction

  task automatic fail(input string message);
    begin
      $display("A2_K2_NORMALIZED_FAIL %s", message);
      $fatal(1);
    end
  endtask

  task automatic reset_dut;
    begin
      @(negedge clk);
      rst_n = 0;
      source_valid = 0;
      retire_ready = 0;
      repeat (2) @(posedge clk);
      #1;
      if (source_ready != 0 || retire_valid != 0 || !drain_idle)
        fail("reset was not quiet and idle");
      @(negedge clk);
      rst_n = 1;
      #1;
    end
  endtask

  // End-to-end conservation includes simultaneous drain/refill edges.
  always @(posedge clk) begin
    if (!rst_n) begin
      accepted_total = 0;
      retired_total = 0;
    end else begin
      accepted_total = accepted_total + popcount16(source_valid & source_ready);
      retired_total = retired_total + popcount2(retire_valid & retire_ready);
      #1;
      if ((accepted_total - retired_total) !== dut.ordered_link.count_q)
        fail("accepted-retired != charged-link occupancy");
    end
  end

  initial begin
    for (source = 0; source < 16; source = source + 1)
      source_event[source] = 16'h1000 + source;

    // count=0: no fabricated source acceptance, completion, or drain work.
    reset_dut();
    if (dut.native_count != 0 || source_ready != 0 || retire_valid != 0 ||
        !drain_idle)
      fail("count0 boundary mismatch");

    // count=1: exact source_ready and payload/source preservation.
    @(negedge clk);
    source_event[3] = 16'hc003;
    source_valid = 16'h0008;
    #1;
    if (dut.native_count != 1 || source_ready != 16'h0008)
      fail("count1 source acceptance mismatch");
    @(posedge clk);
    #1;
    if (retire_valid != 2'b01 || retire_event[0] != 16'hc003 ||
        retire_source[0] != 3 || drain_idle)
      fail("count1 charged capture mismatch");
    @(negedge clk);
    source_valid = 0;
    retire_ready = 2'b01;
    @(posedge clk);
    #1;
    if (retire_valid != 0 || !drain_idle || accepted_total != retired_total)
      fail("count1 drain/conservation mismatch");

    // count=2 and partial-ready: younger-only readiness cannot bypass; a
    // head-only transfer compacts the younger entry, then a count1 offer fills
    // the sole free slot without disturbing order.
    reset_dut();
    @(negedge clk);
    source_event[4] = 16'h4004;
    source_event[8] = 16'h8008;
    source_valid = 16'h0110;
    #1;
    if (dut.native_count != 2 || dut.native_addr0 != 4 ||
        dut.native_addr1 != 8 || source_ready != 16'h0110)
      fail("count2 ordered atomic acceptance mismatch");
    @(posedge clk);
    #1;
    if (retire_valid != 2'b01 || retire_event[0] != 16'h4004 ||
        retire_event[1] != 16'h8008)
      fail("count2 ordered storage mismatch");

    @(negedge clk);
    source_valid = 0;
    retire_ready = 2'b10;
    #1;
    if (retire_valid != 2'b01 || retire_event[0] != 16'h4004)
      fail("younger lane bypassed blocked head");
    @(posedge clk);
    #1;
    if (retire_event[0] != 16'h4004 || dut.ordered_link.count_q != 2)
      fail("blocked ordered pair changed");

    @(negedge clk);
    retire_ready = 2'b01;
    @(posedge clk);
    #1;
    if (retire_valid != 2'b01 || retire_event[0] != 16'h8008 ||
        retire_source[0] != 8 || dut.ordered_link.count_q != 1)
      fail("head-only compaction mismatch");

    @(negedge clk);
    source_event[2] = 16'h2002;
    source_valid = 16'h0004;
    retire_ready = 0;
    #1;
    if (dut.native_count != 1 || source_ready != 16'h0004)
      fail("count1 refill did not fit sole free entry");
    @(posedge clk);
    #1;
    if (dut.ordered_link.count_q != 2 || retire_event[0] != 16'h8008 ||
        retire_event[1] != 16'h2002)
      fail("count1 refill displaced older entry");
    @(negedge clk);
    source_valid = 0;
    retire_ready = 2'b11;
    #1;
    if (retire_valid != 2'b11 || retire_event[0] != 16'h8008 ||
        retire_event[1] != 16'h2002)
      fail("ordered two-lane presentation mismatch");
    @(posedge clk);
    #1;
    if (!drain_idle || accepted_total != retired_total)
      fail("partial-ready sequence failed to drain conservatively");

    // Full-link stall captures and holds the owner's complete K2 offer.  An
    // unrelated request added during the stall must neither replace a held
    // address nor receive source_ready.
    reset_dut();
    @(negedge clk);
    source_event[4] = 16'h4404;
    source_event[8] = 16'h8808;
    source_valid = 16'h0110;
    @(posedge clk);
    #1;
    @(negedge clk);
    source_valid = 16'h0220;
    source_event[5] = 16'h5505;
    source_event[9] = 16'h9909;
    retire_ready = 0;
    #1;
    if (source_ready != 0 || dut.native_count != 2)
      fail("full link partially accepted a K2 offer");
    @(posedge clk);
    #1;
    if (!dut.owner.hold_q)
      fail("owner did not capture stalled offer");
    held_addr0 = dut.native_addr0;
    held_addr1 = dut.native_addr1;

    @(negedge clk);
    source_valid = 16'h0221;
    #1;
    if (source_ready != 0 || dut.native_addr0 != held_addr0 ||
        dut.native_addr1 != held_addr1)
      fail("held offer changed when unrelated work arrived");
    @(posedge clk);
    #1;
    if (dut.native_addr0 != held_addr0 || dut.native_addr1 != held_addr1)
      fail("held offer/order changed across stall edge");

    // One free slot is insufficient for the held pair.
    @(negedge clk);
    retire_ready = 2'b01;
    #1;
    if (source_ready != 0)
      fail("held K2 split across one free slot");
    @(posedge clk);
    #1;
    if (!dut.owner.hold_q || dut.ordered_link.count_q != 1)
      fail("partial drain advanced owner policy");

    // Draining the remaining old head creates room for the entire held pair.
    @(negedge clk);
    retire_ready = 2'b01;
    #1;
    if (source_ready != 16'h0220)
      fail("held K2 did not receive exact atomic source_ready");
    @(posedge clk);
    #1;
    if (dut.owner.hold_q || dut.ordered_link.count_q != 2 ||
        retire_event[0] != 16'h5505 || retire_event[1] != 16'h9909)
      fail("held K2 commit/refill order mismatch");
    @(negedge clk);
    source_valid = 0;
    retire_ready = 2'b11;
    @(posedge clk);
    #1;
    if (!drain_idle || accepted_total != retired_total)
      fail("held-offer sequence failed conservation/drain");

    // Back-to-back full-width replacement: two old retirements and one atomic
    // two-entry owner acceptance share an edge without a bubble or reordering.
    reset_dut();
    @(negedge clk);
    source_event[4] = 16'ha004;
    source_event[8] = 16'ha008;
    source_valid = 16'h0110;
    @(posedge clk);
    #1;
    @(negedge clk);
    source_event[5] = 16'hb005;
    source_event[9] = 16'hb009;
    source_valid = 16'h0220;
    retire_ready = 2'b11;
    #1;
    if (retire_valid != 2'b11 || source_ready != 16'h0220)
      fail("back-to-back full bundle was not simultaneously ready");
    @(posedge clk);
    #1;
    if (dut.ordered_link.count_q != 2 || retire_event[0] != 16'hb005 ||
        retire_event[1] != 16'hb009)
      fail("back-to-back replacement lost order/data");
    @(negedge clk);
    source_valid = 0;
    retire_ready = 2'b11;
    @(posedge clk);
    #1;
    if (!drain_idle || accepted_total != retired_total)
      fail("back-to-back sequence failed conservation/drain");

    // Mid-stall reset discards both owner hold state and charged link state.
    reset_dut();
    @(negedge clk);
    source_valid = 16'h0110;
    retire_ready = 0;
    @(posedge clk);
    #1;
    @(negedge clk);
    source_valid = 16'h0220;
    @(posedge clk);
    #1;
    if (!dut.owner.hold_q || dut.ordered_link.count_q != 2)
      fail("reset setup did not contain owner/link state");
    @(negedge clk);
    rst_n = 0;
    #1;
    if (source_ready != 0 || retire_valid != 0 || !drain_idle)
      fail("reset did not immediately gate normalized boundary");
    @(posedge clk);
    #1;
    if (dut.owner.hold_q || dut.ordered_link.count_q != 0)
      fail("reset edge did not clear owner/link state");
    @(negedge clk);
    source_valid = 0;
    rst_n = 1;
    #1;
    if (source_ready != 0 || retire_valid != 0 || !drain_idle)
      fail("post-reset boundary was not empty");

    $display("A2_K2_NORMALIZED_PASS count0 count1 count2 back_to_back partial_ready hold order source_ready reset drain conservation");
    $finish;
  end
endmodule
