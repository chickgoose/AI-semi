`timescale 1ns/1ps

// Functional-only local models for candidate equivalence tests.  Timing comes
// exclusively from slow_vdd1v0_basicCells.lib in a future authorized flow.
module TLATNCAX2 (
  input  wire CK,
  input  wire E,
  output wire ECK
);
  reg enable_latched;
  always_latch begin
    if (!CK)
      enable_latched = E;
  end
  assign ECK = CK & enable_latched;
endmodule

module TLATNTSCAX2 (
  input  wire CK,
  input  wire E,
  input  wire SE,
  output wire ECK
);
  reg enable_latched;
  always_latch begin
    if (!CK)
      enable_latched = E | SE;
  end
  assign ECK = CK & enable_latched;
endmodule
