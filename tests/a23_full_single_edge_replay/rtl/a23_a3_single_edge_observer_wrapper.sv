`timescale 1ns/1ps

// Zero-state observation binding for the forthcoming charged A3 single-edge
// endpoint.  No P6 logic, receipt, clock, or inferred acceptance is present.
module a23_a3_single_edge_observer_wrapper (
  input  logic        clk_i,
  input  logic        rst_n_i,
  input  logic [15:0] source_pending_i,
  output logic [15:0] source_accept_o,
  output logic        accept_valid_o,
  output logic [1:0]  accept_count_o,
  output logic [3:0]  accept_addr0_o,
  output logic [3:0]  accept_addr1_o,
  output logic [1:0]  retire_valid_o,
  output logic [3:0]  retire_addr0_o,
  output logic [3:0]  retire_addr1_o,
  output logic        protocol_error_o,
  output logic        drain_idle_o
);
  a3_exact_scalar_prefix_single_edge_endpoint dut (
    .clk_i,
    .rst_n_i,
    .source_pending_i,
    .source_accept_o,
    .accept_valid_o,
    .accept_count_o,
    .accept_addr0_o,
    .accept_addr1_o,
    .retire_valid_o,
    .retire_addr0_o,
    .retire_addr1_o,
    .protocol_error_o,
    .drain_idle_o
  );
endmodule
