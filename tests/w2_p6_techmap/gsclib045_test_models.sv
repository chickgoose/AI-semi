`timescale 1ns/1ps

// Functional test models only.  The explicit guard prevents accidental use as
// a production library substitute and is checked by the manifest test.
`ifdef W2_P6_TEST_ONLY
module TLATNTSCAX2 (
  input  logic CK,
  input  logic E,
  input  logic SE,
  output logic ECK
);
  logic enable_latched_q = 1'b0;
  always_latch begin
    if (!CK)
      enable_latched_q = E | SE;
  end
  assign ECK = CK & enable_latched_q;
endmodule

module MX2X1 (
  input  logic A,
  input  logic B,
  input  logic S0,
  output logic Y
);
  assign Y = S0 ? B : A;
endmodule

module DFFRHQX1 (
  input  logic RN,
  input  logic CK,
  input  logic D,
  output logic Q
);
  always_ff @(posedge CK or negedge RN) begin
    if (!RN)
      Q <= 1'b0;
    else
      Q <= D;
  end
endmodule
`endif
