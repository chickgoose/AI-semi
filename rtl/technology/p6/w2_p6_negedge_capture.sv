`timescale 1ns/1ps
`include "w2_p6_tech_select.svh"

// DFFNSRX1 is an explicit user-directed binding. The authoritative Ganghee
// archives do not contain this cell or its Liberty payload, so pin/function,
// recovery/removal, and physical qualification remain HOLD until server proof.
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
`elsif W2_P6_TECH_GENERIC
  always_ff @(negedge clock_i or negedge rst_ni) begin
    if (!rst_ni)
      data_o <= '0;
    else
      data_o <= data_i;
  end
`elsif W2_P6_TECH_GSCLIB045
  for (genvar bit_index = 0; bit_index < WIDTH; bit_index++) begin : gen_capture
    (* keep = "true", dont_touch = "true",
       w2_endpoint_leaf_role = "fall_capture_bit" *)
    DFFNSRX1 w2_ep_neg_bit (
      .CKN(clock_i), .D(data_i[bit_index]), .RN(rst_ni), .SN(1'b1),
      .Q(data_o[bit_index]), .QN()
    );
  end
`endif
endmodule
