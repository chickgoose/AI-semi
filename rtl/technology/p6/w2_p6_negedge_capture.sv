`timescale 1ns/1ps
`include "w2_p6_tech_select.svh"

// No immutable local evidence provides a complete negative-edge async-clear
// cell interface.  This synthesizable edge contract is therefore intentionally
// inferred in both selections and remains an explicit technology-mapping HOLD.
module w2_p6_negedge_capture #(
  parameter int unsigned WIDTH = 1
) (
  input  logic             clock_i,
  input  logic             rst_ni,
  input  logic [WIDTH-1:0] data_i,
  output logic [WIDTH-1:0] data_o
);
`ifdef W2_P6_TECH_SELECTION_ERROR
  w2_p6_invalid_or_missing_technology_selection__compile_error invalid_selection();
  assign data_o = 'x;
`else
  always_ff @(negedge clock_i or negedge rst_ni) begin
    if (!rst_ni)
      data_o <= '0;
    else
      data_o <= data_i;
  end
`endif
endmodule
