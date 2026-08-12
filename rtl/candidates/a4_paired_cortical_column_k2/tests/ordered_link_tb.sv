`timescale 1ns/1ps
/* verilator lint_off DECLFILENAME */

module a4_pcck2_ordered_link_tb;
  logic clk = 0;
  logic rst_n = 0;
  logic [15:0] source_valid = 0;
  logic [15:0] source_ready;
  logic [1:0] retire_valid;
  logic [1:0] retire_ready = 0;
  logic [7:0] retire_addr;
  logic drain_idle;
  logic [7:0] held_addr;
  logic [1:0] held_valid;

  a4_pcck2_ordered_link_adapter dut (.*);
  always #5 clk <= ~clk;

  initial begin
    repeat (2) @(posedge clk);
    @(negedge clk); rst_n = 1; source_valid = 16'hffff; retire_ready = 0; #1;
    if (source_ready == 0) $fatal(1, "first atomic bundle not accepted");
    @(posedge clk); #1;
    @(negedge clk); source_valid = 16'hffff; retire_ready = 0;
    @(posedge clk); #1;
    if (retire_valid != 2'b11) $fatal(1, "two-lane bundle not presented");
    held_addr = retire_addr; held_valid = retire_valid;
    repeat (4) begin
      @(negedge clk); retire_ready = 2'b10;
      @(posedge clk); #1;
      if (retire_valid != held_valid || retire_addr != held_addr)
        $fatal(1, "younger lane bypassed stalled lane0");
      if (source_ready != 0)
        $fatal(1, "scheduler committed into full blocked adapter");
    end
    @(negedge clk); retire_ready = 2'b01;
    @(posedge clk); #1;
    if (!retire_valid[0]) $fatal(1, "remaining event not shifted to lane0");
    @(negedge clk); source_valid = 0; retire_ready = 2'b11;
    repeat (8) @(posedge clk);
    #1;
    if (!drain_idle) $fatal(1, "adapter did not drain");
    $display("A4_PCCK2_ORDERED_LINK_PASS");
    $finish;
  end
endmodule
