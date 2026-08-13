`timescale 1ns/1ps
`include "w2_p6_tech_select.svh"

module w2_p6_mux2 (
  input  logic data0_i,
  input  logic data1_i,
  input  logic select_i,
  output logic data_o
);
`ifdef W2_P6_TECH_SELECTION_ERROR
  w2_p6_invalid_or_missing_technology_selection__compile_error invalid_selection();
  assign data_o = 1'bx;
`elsif W2_P6_TECH_GENERIC
  assign data_o = select_i ? data1_i : data0_i;
`elsif W2_P6_TECH_GSCLIB045
  MX2X1 mux_cell (
    .A(data0_i),
    .B(data1_i),
    .S0(select_i),
    .Y(data_o)
  );
`endif
endmodule
