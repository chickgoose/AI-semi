`timescale 1ns/1ps

module a9_w5_ddr_rx_endpoint #(
  parameter int ADDR_WIDTH = 4,
  parameter int DATA_WIDTH = 2
) (
  input  logic                  burst_clk_i,
  input  logic                  rst_n,
  input  logic [DATA_WIDTH-1:0] burst_data_i,
  output logic [ADDR_WIDTH-1:0] raw_retire_addr_o,
  output logic                  raw_retire_toggle_o
);
  logic [DATA_WIDTH-1:0] rise_data;
  logic [DATA_WIDTH-1:0] fall_data;

  a9_w5_rx_capture #(.DATA_WIDTH(DATA_WIDTH)) rx_capture (
    .burst_clk_i(burst_clk_i),
    .rst_n(rst_n),
    .data_i(burst_data_i),
    .rise_data_o(rise_data),
    .fall_data_o(fall_data)
  );

  // IDDR Q1 changes at the rising edge, but A7 keeps retire_addr_o stable until
  // the falling-edge commit.  A low-phase commit hold admits the complete
  // {fall,rise} pair only after Q2 updates and then closes before the next rise.
  always_latch begin
    if (!rst_n)
      raw_retire_addr_o = '0;
    else if (!burst_clk_i)
      raw_retire_addr_o = {fall_data, rise_data};
  end

  always_ff @(negedge burst_clk_i or negedge rst_n) begin
    if (!rst_n)
      raw_retire_toggle_o <= 1'b0;
    else
      raw_retire_toggle_o <= ~raw_retire_toggle_o;
  end

  initial begin
    if (ADDR_WIDTH != 4)
      $fatal(1, "A9 W5 freezes N16 address width at four bits");
    if (DATA_WIDTH != 2)
      $fatal(1, "A9 W5 requires exactly two data wires");
  end
endmodule
