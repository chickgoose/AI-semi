`timescale 1ns/1ps

module k2_ordered_link_compile_tb;
  localparam int NUM_SOURCES = 16;
  localparam int EVENT_WIDTH = 16;
  localparam int SOURCE_WIDTH = 4;

  logic clk = 1'b0;
  logic rst_n = 1'b0;
  logic [NUM_SOURCES-1:0] source_valid = '0;
  logic [NUM_SOURCES-1:0] source_ready;
  logic [EVENT_WIDTH-1:0] source_event [NUM_SOURCES];
  logic [1:0] offer_count = 2'd0;
  logic [SOURCE_WIDTH-1:0] offer_source0 = '0;
  logic [SOURCE_WIDTH-1:0] offer_source1 = '0;
  logic offer_ready;
  logic scheduler_idle = 1'b1;
  logic [1:0] retire_valid;
  logic [1:0] retire_ready = '0;
  logic [EVENT_WIDTH-1:0] retire_event [2];
  logic [SOURCE_WIDTH-1:0] retire_source [2];
  logic link_empty;
  logic drain_idle;
  integer source_index;

  always #5 clk = ~clk;

  aer_k2_ordered_link_shim #(
    .NUM_SOURCES(NUM_SOURCES),
    .EVENT_WIDTH(EVENT_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH),
    .RETIRE_LANES(2)
  ) dut (.*);

  task automatic drive_offer(
      input logic [1:0] count,
      input logic [3:0] source0,
      input logic [3:0] source1
  );
    begin
      offer_count = count;
      offer_source0 = source0;
      offer_source1 = source1;
      scheduler_idle = (count == 0);
    end
  endtask

  initial begin
    for (source_index = 0; source_index < NUM_SOURCES;
         source_index = source_index + 1)
      source_event[source_index] =
          EVENT_WIDTH'(16'h1000) + EVENT_WIDTH'(source_index);

    repeat (2) @(posedge clk);
    #1;
    if (!link_empty || !drain_idle || source_ready != 0 || retire_valid != 0)
      $fatal(1, "reset did not force quiet drain");

    @(negedge clk);
    rst_n = 1'b1;
    #1;
    if (!drain_idle)
      $fatal(1, "empty reset release was not drained");

    // Malformed counts and duplicate pairs fail closed.
    source_valid = 16'hffff;
    drive_offer(3, 1, 2);
    #1;
    if (offer_ready || source_ready != 0)
      $fatal(1, "illegal count was accepted");
    drive_offer(2, 4, 4);
    #1;
    if (offer_ready || source_ready != 0)
      $fatal(1, "duplicate source pair was accepted");
    source_valid = '0;
    drive_offer(0, 0, 0);

    // A count-two offer is all-or-nothing and captures exact source events.
    source_valid = (16'b1 << 3) | (16'b1 << 12);
    drive_offer(2, 3, 12);
    #1;
    if (!offer_ready || source_ready != ((16'b1 << 3) | (16'b1 << 12)))
      $fatal(1, "count-two source_ready mapping mismatch");
    @(posedge clk);
    #1;
    source_valid = '0;
    source_event[3] = 16'hdead;
    source_event[12] = 16'hbeef;
    drive_offer(0, 0, 0);
    if (retire_valid != 2'b01 || retire_source[0] != 3 ||
        retire_event[0] != 16'h1003)
      $fatal(1, "oldest captured event mismatch");

    // A ready younger lane cannot bypass a blocked head.
    @(negedge clk);
    retire_ready = 2'b10;
    #1;
    if (retire_valid != 2'b01 || retire_source[0] != 3)
      $fatal(1, "younger bypassed blocked head");
    repeat (2) @(posedge clk);
    #1;
    if (retire_source[0] != 3 || retire_event[0] != 16'h1003)
      $fatal(1, "blocked head changed");

    // Retire only the head; the younger entry compacts to lane zero.
    @(negedge clk);
    retire_ready = 2'b01;
    @(posedge clk);
    #1;
    retire_ready = 2'b00;
    if (retire_valid != 2'b01 || retire_source[0] != 12 ||
        retire_event[0] != 16'h100c)
      $fatal(1, "ordered compaction mismatch");

    // A non-live offer is rejected and cannot fabricate source_ready.
    drive_offer(1, 5, 0);
    #1;
    if (offer_ready || source_ready != 0)
      $fatal(1, "non-live offer was accepted");
    source_valid[5] = 1'b1;
    #1;
    if (!offer_ready || source_ready != (16'b1 << 5))
      $fatal(1, "count-one exact acceptance mismatch");
    @(posedge clk);
    #1;
    source_valid = '0;
    drive_offer(0, 0, 0);

    // Both stored events become visible only for a joint ordered transfer.
    @(negedge clk);
    retire_ready = 2'b11;
    #1;
    if (retire_valid != 2'b11 || retire_source[0] != 12 ||
        retire_source[1] != 5 || retire_event[0] != 16'h100c ||
        retire_event[1] != 16'h1005)
      $fatal(1, "two-entry ordered presentation mismatch");
    @(posedge clk);
    #1;
    retire_ready = 2'b00;
    if (!link_empty || !drain_idle || retire_valid != 0)
      $fatal(1, "link failed clean drain");

    // Reset aborts charged work and prevents stale post-reset retirement.
    @(negedge clk);
    source_valid[7] = 1'b1;
    drive_offer(1, 7, 0);
    @(posedge clk);
    #1;
    if (link_empty)
      $fatal(1, "reset test offer was not charged");
    @(negedge clk);
    rst_n = 1'b0;
    #1;
    if (!link_empty || !drain_idle || source_ready != 0 || retire_valid != 0)
      $fatal(1, "reset failed to flush charged work");
    source_valid = '0;
    drive_offer(0, 0, 0);
    @(posedge clk);
    @(negedge clk);
    rst_n = 1'b1;
    #1;
    if (!drain_idle || retire_valid != 0)
      $fatal(1, "stale event leaked after reset");

    $display("K2_ORDERED_LINK_COMPILE_PASS");
    $finish;
  end
endmodule
