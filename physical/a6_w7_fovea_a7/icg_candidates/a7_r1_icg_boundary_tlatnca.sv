`timescale 1ns/1ps

// Candidate A: GPDK045 characterized ICG without a scan/test enable.
// Drop-in only for the A6 synthesis staging copy; owner RTL stays untouched.
module a7_r1_icg_boundary (
  input  logic clock_i,
  input  logic enable_i,
  input  logic rst_n,
  output logic clock_o
);
  wire gate_enable = enable_i & rst_n;

  // slow_vdd1v0_basicCells: CK=input clock, E=input enable,
  // ECK=output gated clock.  Reset release is timed through E.
  TLATNCAX2 characterized_icg (
    .CK  (clock_i),
    .E   (gate_enable),
    .ECK (clock_o)
  );
endmodule
