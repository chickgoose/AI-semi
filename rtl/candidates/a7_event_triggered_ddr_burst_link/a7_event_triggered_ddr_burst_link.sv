`timescale 1ns/1ps

module a7_event_triggered_ddr_burst_link #(
  parameter int ADDR_WIDTH = 4,
  parameter int DATA_WIDTH = 2
) (
  input  logic                  ref_clk_i,
  input  logic                  sample_clk_i,
  input  logic                  rst_n,
  input  logic                  event_valid_i,
  input  logic [ADDR_WIDTH-1:0] event_addr_i,
  output logic                  event_ready_o,
  output logic                  burst_clk_o,
  output logic [DATA_WIDTH-1:0] burst_data_o,
  output logic [ADDR_WIDTH-1:0] retire_addr_o,
  output logic                  retire_toggle_o
);
  a7_ddr_burst_tx #(
    .ADDR_WIDTH(ADDR_WIDTH),
    .DATA_WIDTH(DATA_WIDTH)
  ) tx (
    .ref_clk_i(ref_clk_i),
    .sample_clk_i(sample_clk_i),
    .rst_n(rst_n),
    .event_valid_i(event_valid_i),
    .event_addr_i(event_addr_i),
    .event_ready_o(event_ready_o),
    .burst_clk_o(burst_clk_o),
    .burst_data_o(burst_data_o)
  );

  a7_ddr_burst_rx #(
    .ADDR_WIDTH(ADDR_WIDTH),
    .DATA_WIDTH(DATA_WIDTH)
  ) rx (
    .rst_n(rst_n),
    .burst_clk_i(burst_clk_o),
    .burst_data_i(burst_data_o),
    .retire_addr_o(retire_addr_o),
    .retire_toggle_o(retire_toggle_o)
  );
endmodule
