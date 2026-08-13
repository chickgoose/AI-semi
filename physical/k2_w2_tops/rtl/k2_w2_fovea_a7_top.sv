`timescale 1ns/1ps

// W2 physical wrapper for the complete scalar Fovea plus A7 DDR link.
// This shell is wiring only: it gives the three full-link candidates the same
// clock/reset/offered-load input boundary without changing the owner's native
// output or link-pin contract.
module k2_w2_fovea_a7_top (
  input  logic        ref_clk_i,
  input  logic        sample_clk_i,
  input  logic        rst_n,
  input  logic [15:0] load_i,
  output logic [15:0] source_ready_o,
  output logic        burst_clk_o,
  output logic [1:0]  burst_data_o,
  output logic [3:0]  retire_addr_o,
  output logic        retire_valid_o,
  output logic        drain_idle_o,
  output logic        protocol_fault_o
);
  a7_weighted_fovea_ddr composition (
    .ref_clk_i,
    .sample_clk_i,
    .rst_n,
    .source_valid(load_i),
    .source_ready(source_ready_o),
    .burst_clk_o,
    .burst_data_o,
    .retire_addr_o,
    .retire_valid_o,
    .drain_idle_o,
    .protocol_fault_o
  );
endmodule
