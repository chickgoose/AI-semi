`timescale 1ns/1ps

// Optional adapter suite.  Owners run it only when promotion includes a
// buffered independently-ready downstream contract.  The scheduler-only seam
// remains atomic regardless of this adapter's internal retire movement.
module k2_ordered_link_conformance_tb;
  logic clk = 0;
  logic rst = 1;
  logic [1:0] offer_count = 0;
  logic [3:0] offer_addr0 = 0;
  logic [3:0] offer_addr1 = 0;
  wire offer_ready;
  wire [1:0] retire_valid;
  wire [3:0] retire_addr0;
  wire [3:0] retire_addr1;
  logic [1:0] retire_ready = 0;
  wire link_empty;

  always #5 clk = ~clk;

  k2_ordered_link_binding link (.*);

  task automatic set_offer(
    input logic [1:0] count,
    input logic [3:0] addr0,
    input logic [3:0] addr1,
    input logic [1:0] ready
  );
    begin
      @(negedge clk);
      offer_count = count;
      offer_addr0 = addr0;
      offer_addr1 = addr1;
      retire_ready = ready;
      #1;
    end
  endtask

  initial begin
    repeat (2) @(posedge clk);
    @(negedge clk); rst = 0; #1;
    if ((link_empty !== 1'b1) || (retire_valid !== 0))
      $fatal(1, "K2_LINK_TB reset did not drain link");

    // One atomic count2 offer enters the empty link.
    set_offer(2, 3, 12, 0);
    if (offer_ready !== 1'b1)
      $fatal(1, "K2_LINK_TB empty link rejected count2");
    @(posedge clk); #1;
    offer_count = 0;
    if ((retire_valid !== 2'b01) || (retire_addr0 !== 3))
      $fatal(1, "K2_LINK_TB first ordered head mismatch");

    // A ready younger lane cannot bypass a blocked head.  The presentation and
    // address of the head remain stable for arbitrarily many blocked edges.
    @(negedge clk); retire_ready = 2'b10; #1;
    if (retire_valid !== 2'b01)
      $fatal(1, "K2_LINK_TB younger-lane bypass");
    repeat (2) begin
      @(posedge clk); #1;
      if ((retire_valid !== 2'b01) || (retire_addr0 !== 3))
        $fatal(1, "K2_LINK_TB held retire offer changed");
    end

    // Retire only the head and refill the freed slot on the same edge.  The
    // old younger item must compact ahead of the refill.
    set_offer(1, 7, 0, 2'b01);
    if (offer_ready !== 1'b1)
      $fatal(1, "K2_LINK_TB partial-retire refill was not admitted");
    @(posedge clk); #1;
    offer_count = 0;
    retire_ready = 0;
    #1;
    if ((retire_valid !== 2'b01) || (retire_addr0 !== 12))
      $fatal(1, "K2_LINK_TB ordered compaction failed");
    @(negedge clk); retire_ready = 2'b11; #1;
    if ((retire_valid !== 2'b11) || (retire_addr0 !== 12) ||
        (retire_addr1 !== 7))
      $fatal(1, "K2_LINK_TB compact/refill reordered identities");

    // Full drain and full refill are legal on one edge: no avoidable bubble.
    offer_count = 2;
    offer_addr0 = 5;
    offer_addr1 = 10;
    if (offer_ready !== 1'b1)
      $fatal(1, "K2_LINK_TB back-to-back full refill bubble");
    @(posedge clk); #1;
    offer_count = 0;
    retire_ready = 0;
    #1;
    if ((retire_valid !== 2'b01) || (retire_addr0 !== 5))
      $fatal(1, "K2_LINK_TB refill head mismatch valid=%b addr0=%0d empty=%0d",
             retire_valid, retire_addr0, link_empty);
    @(negedge clk); retire_ready = 2'b11; #1;
    if ((retire_valid !== 2'b11) || (retire_addr0 !== 5) ||
        (retire_addr1 !== 10))
      $fatal(1, "K2_LINK_TB refill order mismatch");
    @(posedge clk); #1;
    retire_ready = 0;
    if (link_empty !== 1'b1)
      $fatal(1, "K2_LINK_TB link did not drain");

    // Reset must abort buffered identities with no stale presentation.
    set_offer(2, 1, 14, 0);
    @(posedge clk); #1;
    offer_count = 0;
    @(negedge clk); rst = 1;
    @(posedge clk); #1;
    if ((link_empty !== 1'b1) || (retire_valid !== 0))
      $fatal(1, "K2_LINK_TB reset leaked buffered identity");

    $display("K2_ORDERED_LINK_CONFORMANCE_PASS partial_compaction=1");
    $finish;
  end
endmodule
