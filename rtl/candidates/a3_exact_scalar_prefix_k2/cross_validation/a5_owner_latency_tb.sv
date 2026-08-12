`timescale 1ns/1ps

module a3_k2_a5_owner_latency_tb;
  logic clk = 0;
  logic rst = 1;
  logic [15:0] source_pending = 16'hffff;
  logic [1:0] grant_count;
  logic [3:0] lane0_addr;
  logic [3:0] lane1_addr;
  logic bundle_ready = 1;

  a3_exact_scalar_prefix_k2 dut (.*);
  always #5 clk = ~clk;

  initial begin
    repeat (2) @(posedge clk);
    @(negedge clk);
    rst = 0;
    // A5 cycle 2: occurrences are visible before this edge, but the owner has
    // no pre-existing registered offer, so no acceptance can occur here.
    if (grant_count != 0)
      $fatal(1, "owner exposed an unregistered same-edge offer");
    @(posedge clk);
    #1;
    if (grant_count != 2 || lane0_addr != 4 || lane1_addr != 11)
      $fatal(1, "owner first registered offer mismatch %0d/%0d/%0d",
             grant_count, lane0_addr, lane1_addr);
    if (dut.round_state != 0)
      $fatal(1, "policy advanced before registered offer commit");

    // A5 cycle 3 is the earliest edge at which that offer can commit.
    @(posedge clk);
    #1;
    if (dut.round_state != 2)
      $fatal(1, "registered two-grant commit did not advance two microsteps");
    $display("A3_K2_A5_OWNER_REGISTERED_LATENCY_PASS first_accept_cycle=3");
    $finish;
  end
endmodule
