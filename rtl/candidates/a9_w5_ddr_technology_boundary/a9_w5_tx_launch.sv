`timescale 1ns/1ps
`include "a9_w5_tech_select.svh"

module a9_w5_tx_launch #(
  parameter int DATA_WIDTH = 2
) (
  input  logic                  ref_clk_i,
  input  logic                  rst_n,
  input  logic                  launch_fire_i,
  input  logic [DATA_WIDTH-1:0] admitted_low_i,
  input  logic [DATA_WIDTH-1:0] held_low_i,
  input  logic [DATA_WIDTH-1:0] held_high_i,
  output logic [DATA_WIDTH-1:0] data_o
);
`ifdef A9_W5_TECH_SELECTION_ERROR
  a9_w5_invalid_or_missing_technology_selection__compile_error invalid_selection();
  assign data_o = 'x;
`elsif A9_W5_TECH_GENERIC
  // Exact A7 generic boundary: the registered address is selected by the
  // level of ref_clk_i.  admitted_low_i is intentionally unused here.
  always_comb begin
    if (ref_clk_i)
      data_o = held_low_i;
    else
      data_o = held_high_i;
  end
`elsif A9_W5_TECH_ASIC
  // D_rise samples the current admitted address because the address holding
  // register updates on the same edge.  D_fall uses that holding register.
  a9_w5_asic_oddr2_cell_adapter oddr (
    .clock_i(ref_clk_i),
    .rst_n(rst_n),
    .d_rise_i(launch_fire_i ? admitted_low_i : held_low_i),
    .d_fall_i(held_high_i),
    .q_o(data_o)
  );
`elsif A9_W5_TECH_XILINX_7SERIES
  genvar bit_index;
  generate
    for (bit_index = 0; bit_index < DATA_WIDTH; bit_index++) begin : gen_oddr
      ODDR #(
        .DDR_CLK_EDGE("OPPOSITE_EDGE"),
        .INIT(1'b0),
        .SRTYPE("ASYNC")
      ) oddr (
        .Q(data_o[bit_index]),
        .C(ref_clk_i),
        .CE(1'b1),
        .D1(launch_fire_i ? admitted_low_i[bit_index] : held_low_i[bit_index]),
        .D2(held_high_i[bit_index]),
        .R(~rst_n),
        .S(1'b0)
      );
    end
  endgenerate
`endif

  initial begin
    if (DATA_WIDTH != 2)
      $fatal(1, "A9 W5 freezes the A7 link at two data wires");
  end
endmodule
