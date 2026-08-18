`timescale 1ns/1ps

// Zero-state observation binding for the forthcoming charged A2 single-edge
// endpoint.  The endpoint, not this wrapper, owns admission, buffering,
// retirement, error, and drain semantics.
module a23_a2_single_edge_observer_wrapper (
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
  logic link_valid;
  logic [3:0] link_addr0;
  logic [3:0] link_addr1;

  a2_batched_iwrr_single_edge_top dut (
    .clk_i,
    .rst_i(!rst_n_i),
    .link_enable_i(1'b1),
    .source_pending_i,
    .source_accept_o,
    .accept_count_o,
    .accept_addr0_o,
    .accept_addr1_o,
    .link_valid_o(link_valid),
    .link_addr0_o(link_addr0),
    .link_addr1_o(link_addr1),
    .retire_valid_o,
    .retire_addr0_o,
    .retire_addr1_o,
    .protocol_error_o,
    .drain_idle_o
  );

  assign accept_valid_o = (accept_count_o != 2'd0);
endmodule
