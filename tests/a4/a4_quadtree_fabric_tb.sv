`timescale 1ns/1ps

module a4_quadtree_fabric_tb;
  localparam int NUM_SOURCES = 16;
  localparam int ADDR_WIDTH = 16;
  localparam int SOURCE_WIDTH = 4;

  logic clk = 1'b0;
  logic rst_n;
  logic [NUM_SOURCES-1:0] source_valid;
  logic [NUM_SOURCES-1:0] source_ready;
  logic [ADDR_WIDTH-1:0] source_event [NUM_SOURCES];
  logic retire_valid;
  logic retire_ready;
  logic [ADDR_WIDTH-1:0] retire_event;
  logic [SOURCE_WIDTH-1:0] retire_source;
  logic [NUM_SOURCES-1:0] seen;
  integer accepted_count;
  integer delivered_count;
  integer first_accept_count;
  integer cycle_count;
  integer last_delivery_cycle;
  integer accepted_this_edge;
  integer i;

  always #5 clk = ~clk;

  a4_quadtree_fabric dut (
    .clk(clk),
    .rst_n(rst_n),
    .source_valid(source_valid),
    .source_ready(source_ready),
    .source_event(source_event),
    .retire_valid(retire_valid),
    .retire_ready(retire_ready),
    .retire_event(retire_event),
    .retire_source(retire_source)
  );

  always @(posedge clk) begin
    if (!rst_n) begin
      seen <= '0;
      accepted_count <= 0;
      delivered_count <= 0;
      first_accept_count <= -1;
      cycle_count <= 0;
      last_delivery_cycle <= -1;
    end else begin
      cycle_count <= cycle_count + 1;
      accepted_this_edge = 0;
      for (i = 0; i < NUM_SOURCES; i = i + 1) begin
        if (source_valid[i] && source_ready[i]) begin
          source_valid[i] <= 1'b0;
          accepted_count <= accepted_count + accepted_this_edge + 1;
          accepted_this_edge = accepted_this_edge + 1;
        end
      end
      if (first_accept_count < 0 && accepted_this_edge > 0)
        first_accept_count <= accepted_this_edge;

      if (retire_valid && retire_ready) begin
        if (retire_source >= NUM_SOURCES)
          $fatal(1, "A4_TREE_TB illegal retire source %0d", retire_source);
        if (seen[retire_source])
          $fatal(1, "A4_TREE_TB duplicate source %0d", retire_source);
        if (retire_event != (16'ha400 + retire_source))
          $fatal(1, "A4_TREE_TB corrupt event for source %0d", retire_source);
        seen[retire_source] <= 1'b1;
        delivered_count <= delivered_count + 1;
        last_delivery_cycle <= cycle_count;
      end
    end
  end

  initial begin
    rst_n = 1'b0;
    source_valid = '0;
    retire_ready = 1'b1;
    for (i = 0; i < NUM_SOURCES; i = i + 1)
      source_event[i] = 16'ha400 + i;

    repeat (2) @(posedge clk);
    #1;
    if (retire_valid)
      $fatal(1, "A4_TREE_TB phantom event after reset");
    rst_n = 1'b1;
    source_valid = '1;

    wait (delivered_count == NUM_SOURCES);
    @(posedge clk);
    #1;
    if (first_accept_count != 4)
      $fatal(1, "A4_TREE_TB first edge accepted %0d, expected 4",
             first_accept_count);
    if (accepted_count != NUM_SOURCES)
      $fatal(1, "A4_TREE_TB accepted %0d, expected %0d",
             accepted_count, NUM_SOURCES);
    if (seen != '1)
      $fatal(1, "A4_TREE_TB missing retirements seen=%h", seen);
    if (last_delivery_cycle > 21)
      $fatal(1, "A4_TREE_TB bounded progress exceeded: cycle %0d",
             last_delivery_cycle);
    if (retire_valid)
      $fatal(1, "A4_TREE_TB failed to drain quiet");

    $display("A4_TREE_TB PASS accepted=%0d delivered=%0d last_cycle=%0d",
             accepted_count, delivered_count, last_delivery_cycle);
    $finish;
  end

  initial begin
    repeat (64) @(posedge clk);
    $fatal(1, "A4_TREE_TB timeout");
  end
endmodule
