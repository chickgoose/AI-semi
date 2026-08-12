`timescale 1ns/1ps

module a3_k2_a5_link_tb;
  logic clk = 0;
  logic rst = 1;
  logic [1:0] offer_count = 0;
  logic [3:0] offer_addr0 = 0;
  logic [3:0] offer_addr1 = 0;
  logic offer_ready;
  logic [1:0] retire_valid;
  logic [3:0] retire_addr0;
  logic [3:0] retire_addr1;
  logic [1:0] retire_ready = 0;
  logic link_empty;

  a3_k2_ordered_link_adapter dut (.*);
  always #5 clk = ~clk;

  initial begin
    repeat (2) @(posedge clk);
    @(negedge clk);
    rst = 0;
    offer_count = 2;
    offer_addr0 = 4;
    offer_addr1 = 11;
    #1;
    if (!offer_ready)
      $fatal(1, "empty charged link rejected full offer");
    @(posedge clk);
    #1;
    if (link_empty || retire_valid != 2'b01 || retire_addr0 != 4)
      $fatal(1, "first atomic offer was not stored");

    // A ready younger lane cannot bypass a blocked head, and no second full
    // scheduler offer fits while the first remains buffered.
    @(negedge clk);
    offer_addr0 = 5;
    offer_addr1 = 10;
    retire_ready = 2'b10;
    #1;
    if (retire_valid != 2'b01 || offer_ready)
      $fatal(1, "younger bypass or free capacity fabricated");
    repeat (3) @(posedge clk);
    #1;
    if (retire_addr0 != 4)
      $fatal(1, "blocked head changed");

    // Retire only the head.  The hidden younger entry compacts to lane 0.
    @(negedge clk);
    retire_ready = 2'b01;
    @(posedge clk);
    #1;
    if (retire_addr0 != 11 || retire_valid != 2'b01)
      $fatal(1, "ordered compaction failed");

    // A one-entry offer is admitted into the sole free slot and charged.
    @(negedge clk);
    offer_count = 1;
    offer_addr0 = 5;
    retire_ready = 0;
    #1;
    if (!offer_ready)
      $fatal(1, "one-entry offer did not fit");
    @(posedge clk);
    #1;
    if (retire_addr0 != 11)
      $fatal(1, "older entry displaced by refill");

    @(negedge clk);
    offer_count = 0;
    retire_ready = 2'b11;
    #1;
    if (retire_valid != 2'b11 || retire_addr0 != 11 || retire_addr1 != 5)
      $fatal(1, "two-entry ordered presentation mismatch");
    @(posedge clk);
    #1;
    if (!link_empty)
      $fatal(1, "link failed to drain");
    $display("A3_K2_A5_CHARGED_LINK_PASS state_bits=10");
    $finish;
  end
endmodule
