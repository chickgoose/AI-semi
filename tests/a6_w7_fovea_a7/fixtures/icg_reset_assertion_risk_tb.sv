`timescale 1ns/1ps

// This is deliberately a risk proof, not a replacement contract.  The owner
// boundary asynchronously truncates clock_o when reset asserts high-phase;
// a reset-free characterized ICG fed only through E cannot do that until CK
// returns low.  The production contract must keep reset assertion/release in
// the legal low phase unless a reset-capable ICG or explicit arming redesign
// is selected and requalified.
module icg_reset_assertion_risk_tb;
  logic clock_i = 1'b0;
  logic enable_i = 1'b0;
  logic rst_n = 1'b0;
  wire owner_clock_o;
  wire candidate_clock_o;
  logic owner_enable_latched = 1'b0;

  always_latch begin
    if (!rst_n)
      owner_enable_latched = 1'b0;
    else if (!clock_i)
      owner_enable_latched = enable_i;
  end
  assign owner_clock_o = clock_i & owner_enable_latched & rst_n;

  TLATNCAX2 candidate (
    .CK(clock_i),
    .E(enable_i & rst_n),
    .ECK(candidate_clock_o)
  );

  initial begin
    #1 rst_n = 1'b1;
    enable_i = 1'b1;
    #3 clock_i = 1'b1;
    #1;
    if (owner_clock_o !== 1'b1 || candidate_clock_o !== 1'b1)
      $fatal(1, "ICG risk fixture failed to arm both models");
    #1 rst_n = 1'b0;
    #1;
    if (owner_clock_o !== 1'b0 || candidate_clock_o !== 1'b1)
      $fatal(1, "high-phase asynchronous assertion risk was not observed");
    $display("W7_ICG_ASYNC_ASSERTION_RISK_PROVEN owner=0 candidate=1");
    $finish;
  end
endmodule
