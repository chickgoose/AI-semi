`timescale 1ns/1ps
`include "w2_p6_tech_select.svh"

// The generic branch preserves the frozen owner's exact Boolean expression.
// The cell branch uses the locally evidenced characterized ICG and is
// equivalent under the owner's legal drained, clock-low reset contract.
module w2_p6_clock_boundary (
  input  logic clock_i,
  input  logic enable_i,
  output logic clock_o
);
`ifdef W2_P6_TECH_SELECTION_ERROR
  w2_p6_invalid_or_missing_technology_selection__compile_error invalid_selection();
  assign clock_o = 1'bx;
`elsif W2_P6_TECH_GENERIC
  assign clock_o = clock_i & enable_i;
`elsif W2_P6_TECH_GSCLIB045
  TLATNCAX2 clock_gate_cell (
    .CK(clock_i),
    .E(enable_i),
    .ECK(clock_o)
  );
`endif
endmodule
