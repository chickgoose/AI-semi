`timescale 1ns/1ps

// Immediate-and-sticky protocol indication shared by the complete tops.
// Reset clears history synchronously; qualification owns reset-before-drain.
module w2_single_edge_error_latch (
  input  logic clk_i,
  input  logic rst_i,
  input  logic error_event_i,
  output logic protocol_error_o
);
  logic protocol_error_q;

  assign protocol_error_o = protocol_error_q || error_event_i;

  always_ff @(posedge clk_i) begin
    if (rst_i)
      protocol_error_q <= 1'b0;
    else if (error_event_i)
      protocol_error_q <= 1'b1;
  end
endmodule
