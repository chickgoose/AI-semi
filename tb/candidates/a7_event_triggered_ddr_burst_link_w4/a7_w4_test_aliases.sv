`timescale 1ns/1ps

// Test-only aliases let W4 execute the exact committed W3 lockstep/fault TB
// without editing either W3 RTL or its testbench.
module a7_ddr_burst_tx #(
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
  output logic [DATA_WIDTH-1:0] burst_data_o
);
  a7_w4_ddr_tx #(.ADDR_WIDTH(ADDR_WIDTH), .DATA_WIDTH(DATA_WIDTH)) impl (.*);
endmodule

module a7_ddr_burst_rx #(
  parameter int ADDR_WIDTH = 4,
  parameter int DATA_WIDTH = 2
) (
  input  logic                  rst_n,
  input  logic                  burst_clk_i,
  input  logic [DATA_WIDTH-1:0] burst_data_i,
  output logic [ADDR_WIDTH-1:0] retire_addr_o,
  output logic                  retire_toggle_o
);
  a7_w4_ddr_rx #(.ADDR_WIDTH(ADDR_WIDTH), .DATA_WIDTH(DATA_WIDTH)) impl (.*);
endmodule
