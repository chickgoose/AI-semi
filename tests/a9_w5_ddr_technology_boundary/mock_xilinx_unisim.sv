`timescale 1ns/1ps

// Test-only functional subset of the 7-series UNISIM interfaces used by W5.
// It proves wrapper wiring and edge identity, not Vivado primitive timing.
module BUFGCE (input logic I, input logic CE, output logic O);
  logic ce_latched_q = 1'b0;
  always_latch begin
    if (!I)
      ce_latched_q = CE;
  end
  assign O = I & ce_latched_q;
endmodule

module ODDR #(
  parameter DDR_CLK_EDGE = "OPPOSITE_EDGE",
  parameter INIT = 1'b0,
  parameter SRTYPE = "ASYNC"
) (
  output logic Q,
  input logic C,
  input logic CE,
  input logic D1,
  input logic D2,
  input logic R,
  input logic S
);
  initial Q = INIT;
  always @(posedge C or posedge R or posedge S) begin
    if (R) Q <= 1'b0;
    else if (S) Q <= 1'b1;
    else if (CE) Q <= D1;
  end
  always @(negedge C or posedge R or posedge S) begin
    if (R) Q <= 1'b0;
    else if (S) Q <= 1'b1;
    else if (CE) Q <= D2;
  end
endmodule

module IDDR #(
  parameter DDR_CLK_EDGE = "OPPOSITE_EDGE",
  parameter INIT_Q1 = 1'b0,
  parameter INIT_Q2 = 1'b0,
  parameter SRTYPE = "ASYNC"
) (
  output logic Q1,
  output logic Q2,
  input logic C,
  input logic CE,
  input logic D,
  input logic R,
  input logic S
);
  initial begin Q1 = INIT_Q1; Q2 = INIT_Q2; end
  always @(posedge C or posedge R or posedge S) begin
    if (R) Q1 <= 1'b0;
    else if (S) Q1 <= 1'b1;
    else if (CE) Q1 <= D;
  end
  always @(negedge C or posedge R or posedge S) begin
    if (R) Q2 <= 1'b0;
    else if (S) Q2 <= 1'b1;
    else if (CE) Q2 <= D;
  end
endmodule
