`timescale 1ns/1ps

// Candidate B: GPDK045 characterized ICG with its test enable tied inactive.
// Drop-in only for the A6 synthesis staging copy; owner RTL stays untouched.
module a7_r1_icg_boundary (
  input  logic clock_i,
  input  logic enable_i,
  input  logic rst_n,
  output logic clock_o
);
  wire gate_enable = enable_i & rst_n;

  // slow_vdd1v0_basicCells: CK=input clock, E=functional enable,
  // SE=test enable, ECK=output gated clock.  There is no new top-level wire.
  TLATNTSCAX2 characterized_icg (
    .CK  (clock_i),
    .E   (gate_enable),
    .SE  (1'b0),
    .ECK (clock_o)
  );
endmodule
