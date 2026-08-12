`timescale 1ns/1ps

module a3_k2_ordered_adapter_tb;
  logic clk = 1'b0;
  logic rst = 1'b1;
  logic [1:0] offer_count = 2'd0;
  logic [3:0] offer_source0 = 4'd0;
  logic [3:0] offer_source1 = 4'd0;
  logic [15:0] offer_event0 = 16'd0;
  logic [15:0] offer_event1 = 16'd0;
  logic offer_ready;
  logic [1:0] retire_valid;
  logic [3:0] retire_source0;
  logic [3:0] retire_source1;
  logic [15:0] retire_event0;
  logic [15:0] retire_event1;
  logic [1:0] retire_ready = 2'b00;
  logic empty;

  a3_k2_ordered_2entry_adapter #(
    .ADDR_WIDTH(16),
    .SOURCE_WIDTH(4)
  ) dut (.*);

  always #5 clk = ~clk;

  initial begin
    repeat (2) @(posedge clk);
    @(negedge clk);
    rst = 1'b0;
    offer_count = 2'd2;
    offer_source0 = 4'd4;
    offer_source1 = 4'd11;
    offer_event0 = 16'ha104;
    offer_event1 = 16'hb10b;
    #1;
    if (!offer_ready)
      $fatal(1, "empty adapter rejected atomic count=2 offer");

    @(posedge clk);
    #1;
    offer_count = 2'd0;
    if (empty || retire_valid != 2'b01 ||
        retire_source0 != 4'd4 || retire_event0 != 16'ha104)
      $fatal(1, "first ordered head was not stored");

    // A ready younger lane cannot bypass a blocked head.  The visible head
    // and its payload must remain stable across a multi-cycle stall.
    @(negedge clk);
    retire_ready = 2'b10;
    repeat (3) begin
      @(posedge clk);
      #1;
      if (retire_valid != 2'b01 || retire_source0 != 4'd4 ||
          retire_event0 != 16'ha104)
        $fatal(1, "head changed or younger bypassed during stall");
    end

    // Retire only the head; the younger entry must compact to lane 0.
    @(negedge clk);
    retire_ready = 2'b01;
    @(posedge clk);
    #1;
    if (retire_valid != 2'b01 || retire_source0 != 4'd11 ||
        retire_event0 != 16'hb10b)
      $fatal(1, "ordered one-entry compaction failed");

    // Refill the sole free slot with a fitting one-entry owner offer.
    @(negedge clk);
    retire_ready = 2'b00;
    offer_count = 2'd1;
    offer_source0 = 4'd5;
    offer_event0 = 16'hc205;
    #1;
    if (!offer_ready)
      $fatal(1, "one-entry refill did not fit");
    @(posedge clk);
    #1;
    offer_count = 2'd0;
    if (retire_source0 != 4'd11 || retire_event0 != 16'hb10b)
      $fatal(1, "refill displaced the older entry");

    @(negedge clk);
    retire_ready = 2'b11;
    #1;
    if (retire_valid != 2'b11 || retire_source0 != 4'd11 ||
        retire_source1 != 4'd5 || retire_event0 != 16'hb10b ||
        retire_event1 != 16'hc205)
      $fatal(1, "two-lane ordered presentation mismatch");
    @(posedge clk);
    #1;
    if (!empty || retire_valid != 2'b00)
      $fatal(1, "adapter failed to drain");

    // With exactly one buffered entry, lane-1 ready is irrelevant.  Toggling
    // it while lane 0 is blocked cannot expose or consume anything, and lane 0
    // consumes the singleton whether lane 1 is ready or not.
    @(negedge clk);
    retire_ready = 2'b00;
    offer_count = 2'd1;
    offer_source0 = 4'd9;
    offer_event0 = 16'h9909;
    @(posedge clk);
    #1;
    offer_count = 2'd0;
    if (retire_valid != 2'b01 || retire_source0 != 4'd9 ||
        retire_event0 != 16'h9909)
      $fatal(1, "count1 setup failed");
    @(negedge clk);
    retire_ready = 2'b10;
    @(posedge clk);
    #1;
    if (retire_valid != 2'b01 || retire_source0 != 4'd9 ||
        retire_event0 != 16'h9909)
      $fatal(1, "count1 incorrectly depended on lane1 ready while stalled");
    @(negedge clk);
    retire_ready = 2'b01;
    @(posedge clk);
    #1;
    if (!empty || retire_valid != 2'b00)
      $fatal(1, "count1 lane0 handshake depended on lane1 ready");

    // Reset must discard charged transport state and produce no phantom.
    @(negedge clk);
    retire_ready = 2'b00;
    offer_count = 2'd1;
    offer_source0 = 4'd7;
    offer_event0 = 16'hd307;
    @(posedge clk);
    #1;
    if (empty)
      $fatal(1, "reset setup entry was not buffered");
    @(negedge clk);
    offer_count = 2'd0;
    rst = 1'b1;
    @(posedge clk);
    #1;
    if (!empty || retire_valid != 2'b00)
      $fatal(1, "reset did not flush adapter state");

    $display("A3_K2_ORDERED_ADAPTER_PASS");
    $finish;
  end
endmodule
