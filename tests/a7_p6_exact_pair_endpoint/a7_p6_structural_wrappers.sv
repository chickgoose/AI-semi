`timescale 1ns/1ps

module a7_p6_structural_top (
  input  logic       ref_clk_i,
  input  logic       sample_clk_i,
  input  logic       rst_n,
  input  logic       input_valid_i,
  input  logic [1:0] input_count_i,
  input  logic [3:0] input_addr0_i,
  input  logic [3:0] input_addr1_i,
  output logic       input_ready_o,
  output logic       input_protocol_error_o,
  output logic       p6_clk_o,
  output logic [4:0] p6_data_o,
  output logic [1:0] retire_valid_o,
  output logic [3:0] retire_addr0_o,
  output logic [3:0] retire_addr1_o,
  output logic       retire_protocol_error_o,
  output logic       drain_idle_o
);
  a7_p6_exact_pair_endpoint endpoint (.*);
endmodule

module a7_p6_parallel_structural_top (
  input  logic       ref_clk_i,
  input  logic       sample_clk_i,
  input  logic       rst_n,
  input  logic       input_valid_i,
  input  logic [1:0] input_count_i,
  input  logic [3:0] input_addr0_i,
  input  logic [3:0] input_addr1_i,
  output logic       input_ready_o,
  output logic       input_protocol_error_o,
  output logic       parallel_strobe_o,
  output logic       parallel_pair_o,
  output logic [3:0] parallel_addr0_o,
  output logic [3:0] parallel_addr1_o,
  output logic [1:0] retire_valid_o,
  output logic [3:0] retire_addr0_o,
  output logic [3:0] retire_addr1_o,
  output logic       retire_protocol_error_o,
  output logic       drain_idle_o
);
  a7_p6_exact_pair_parallel_reference endpoint (.*);
endmodule
