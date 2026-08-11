`timescale 1ns/1ps

// Explicit technology boundary for a characterized integrated clock-gating
// cell. The generic implementation is synthesizable and models the essential
// ICG invariant: enable is captured only while the source clock is low.
// ASIC integration must replace or map this module as one indivisible ICG.
module a7_w4_icg_boundary (
  input  logic clock_i,
  input  logic enable_i,
  input  logic rst_n,
  output logic clock_o
);
  logic enable_latched_q;

  always_latch begin
    if (!rst_n)
      enable_latched_q = 1'b0;
    else if (!clock_i)
      enable_latched_q = enable_i;
  end

  // Reset is required to be asserted only with an idle/drained link. An
  // assertion during clock high can truncate a pulse and is outside delivery
  // guarantees; the candidate-only fault monitor keeps that pulse visible.
  assign clock_o = clock_i & enable_latched_q & rst_n;
endmodule
