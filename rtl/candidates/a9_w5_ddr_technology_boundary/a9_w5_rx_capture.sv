`timescale 1ns/1ps
`include "a9_w5_tech_select.svh"

module a9_w5_rx_capture #(
  parameter int DATA_WIDTH = 2
) (
  input  logic                  burst_clk_i,
  input  logic                  rst_n,
  input  logic [DATA_WIDTH-1:0] data_i,
  output logic [DATA_WIDTH-1:0] rise_data_o,
  output logic [DATA_WIDTH-1:0] fall_data_o
);
`ifdef A9_W5_TECH_SELECTION_ERROR
  a9_w5_invalid_or_missing_technology_selection__compile_error invalid_selection();
  assign rise_data_o = 'x;
  assign fall_data_o = 'x;
`elsif A9_W5_TECH_GENERIC
  always_ff @(posedge burst_clk_i or negedge rst_n) begin
    if (!rst_n)
      rise_data_o <= '0;
    else
      rise_data_o <= data_i;
  end

  always_ff @(negedge burst_clk_i or negedge rst_n) begin
    if (!rst_n)
      fall_data_o <= '0;
    else
      fall_data_o <= data_i;
  end
`elsif A9_W5_TECH_ASIC
  a9_w5_asic_iddr2_cell_adapter iddr (
    .clock_i(burst_clk_i),
    .rst_n(rst_n),
    .d_i(data_i),
    .q_rise_o(rise_data_o),
    .q_fall_o(fall_data_o)
  );
`elsif A9_W5_TECH_XILINX_7SERIES
  genvar bit_index;
  generate
    for (bit_index = 0; bit_index < DATA_WIDTH; bit_index++) begin : gen_iddr
      IDDR #(
        .DDR_CLK_EDGE("OPPOSITE_EDGE"),
        .INIT_Q1(1'b0),
        .INIT_Q2(1'b0),
        .SRTYPE("ASYNC")
      ) iddr (
        .Q1(rise_data_o[bit_index]),
        .Q2(fall_data_o[bit_index]),
        .C(burst_clk_i),
        .CE(1'b1),
        .D(data_i[bit_index]),
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
