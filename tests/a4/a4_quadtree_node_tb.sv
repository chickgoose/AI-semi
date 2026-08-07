`timescale 1ns/1ps

module a4_quadtree_node_tb;
  localparam int RADIX = 4;
  localparam int ADDR_WIDTH = 16;
  localparam int SOURCE_WIDTH = 4;
  localparam int AGE_WIDTH = 8;

  logic clk = 1'b0;
  logic rst_n;
  logic [RADIX-1:0] child_valid;
  logic [RADIX-1:0] child_ready;
  logic [ADDR_WIDTH-1:0] child_event [RADIX];
  logic [SOURCE_WIDTH-1:0] child_source [RADIX];
  logic [AGE_WIDTH-1:0] child_age [RADIX];
  logic out_valid;
  logic out_ready;
  logic [ADDR_WIDTH-1:0] out_event;
  logic [SOURCE_WIDTH-1:0] out_source;
  logic [AGE_WIDTH-1:0] out_age;
  integer i;

  always #5 clk = ~clk;

  a4_quadtree_node dut (
    .clk(clk),
    .rst_n(rst_n),
    .child_valid(child_valid),
    .child_ready(child_ready),
    .child_event(child_event),
    .child_source(child_source),
    .child_age(child_age),
    .out_valid(out_valid),
    .out_ready(out_ready),
    .out_event(out_event),
    .out_source(out_source),
    .out_age(out_age)
  );

  task automatic check(input logic condition, input string message);
    if (!condition)
      $fatal(1, "A4_NODE_TB %s", message);
  endtask

  initial begin
    rst_n = 1'b0;
    child_valid = '0;
    out_ready = 1'b0;
    for (i = 0; i < RADIX; i = i + 1) begin
      child_event[i] = 16'h1000 + i;
      child_source[i] = SOURCE_WIDTH'(i);
      child_age[i] = AGE_WIDTH'(i);
    end

    repeat (2) @(posedge clk);
    #1;
    check(!out_valid, "reset must clear output valid");
    rst_n = 1'b1;

    // All children contend. Reset phase must select child 0 exactly once.
    child_valid = 4'b1111;
    #1;
    check(child_ready == 4'b0001, "initial RR selection must be child 0");
    @(posedge clk);
    #1;
    check(out_valid && out_source == 0 && out_event == 16'h1000,
          "child 0 capture failed");
    check(out_age == 1, "age must increment on a tree hop");

    // A stalled slot must acknowledge nobody and hold all output fields.
    child_event[1] = 16'h2bad;
    #1;
    check(child_ready == 4'b0000, "full stalled node acknowledged a child");
    repeat (2) begin
      @(posedge clk);
      #1;
      check(out_valid && out_source == 0 && out_event == 16'h1000 && out_age == 1,
            "stalled output changed");
    end

    // Pop child 0 and capture child 1 on the same edge, with no bubble.
    out_ready = 1'b1;
    #1;
    check(child_ready == 4'b0010, "RR did not advance to child 1 on refill");
    @(posedge clk);
    #1;
    check(out_valid && out_source == 1 && out_event == 16'h2bad,
          "back-to-back refill lost child 1");

    // Continue through children 2, 3, then wrap to 0.
    #1;
    check(child_ready == 4'b0100, "RR did not advance to child 2");
    @(posedge clk);
    #1;
    check(out_source == 2, "child 2 capture failed");
    check(child_ready == 4'b1000, "RR did not advance to child 3");
    @(posedge clk);
    #1;
    check(out_source == 3, "child 3 capture failed");
    check(child_ready == 4'b0001, "RR did not wrap to child 0");
    @(posedge clk);
    #1;
    check(out_source == 0, "wrapped child 0 capture failed");

    // Drain without replacement and prove the output becomes quiet.
    child_valid = '0;
    @(posedge clk);
    #1;
    check(!out_valid && child_ready == '0, "node did not drain to quiet");
    $display("A4_NODE_TB PASS");
    $finish;
  end
endmodule
