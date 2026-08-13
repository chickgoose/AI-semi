`timescale 1ns/1ps
`include "w2_p6_tech_select.svh"

// The generic path instantiates the frozen R1 latch-and-AND owner boundary.
// The GSCLIB path uses only the authoritative mapped ICG interface.
module w2_r1_clock_boundary (
  input  logic clock_i,
  input  logic enable_i,
  input  logic rst_n,
  output logic clock_o
);
`ifdef W2_P6_TECH_SELECTION_ERROR
  w2_p6_invalid_or_missing_technology_selection__compile_error invalid_selection();
  assign clock_o = 1'bx;
`elsif W2_P6_TECH_GENERIC
  a7_r1_icg_boundary generic_boundary (.*);
`elsif W2_P6_TECH_GSCLIB045
  (* keep = "true", dont_touch = "true",
     w2_endpoint_leaf_role = "clock_gate" *)
  TLATNTSCAX2 w2_ep_icg_0 (
    .CK(clock_i),
    .E(enable_i & rst_n),
    .SE(1'b0),
    .ECK(clock_o)
  );
`endif
endmodule
