`timescale 1ns/1ps

// Scheduler-neutral, always-ready, synthesizable single-edge fallback.
//
// A commit on edge N is launched by the TX register and captured by the RX
// register on edge N+1.  The registered retire record is consumed at the
// synchronous retire boundary on the following edge.  Back-to-back atomic
// records are supported.  link_enable_i blocks only new commits; an already
// launched record continues to retirement.
//
// rst_i is synchronous and active high.  It clears TX, RX, retire, and sticky
// error state on its sampled rising edge.  Therefore reset aborts any in-flight
// accepted record; a lossless system must wait for drain_idle_o before reset.
// No hidden history bit attempts to diagnose an early reset after reset has
// cleared state: qualification is responsible for rejecting reset-before-drain.
module w2_single_edge_exact_pair_endpoint (
  input  logic       clk_i,
  input  logic       rst_i,
  input  logic       link_enable_i,
  input  logic [1:0] input_count_i,
  input  logic [3:0] input_addr0_i,
  input  logic [3:0] input_addr1_i,
  output logic       input_ready_o,
  output logic       input_commit_o,
  output logic [1:0] policy_microsteps_o,
  output logic       protocol_error_o,
  output logic       link_valid_o,
  output logic [3:0] link_addr0_o,
  output logic [3:0] link_addr1_o,
  output logic [1:0] retire_valid_o,
  output logic [3:0] retire_addr0_o,
  output logic [3:0] retire_addr1_o,
  output logic       drain_idle_o
);
  logic tx_protocol_error;
  logic rx_protocol_error;

  w2_single_edge_pair_tx tx (
    .clk_i,
    .rst_i,
    .link_enable_i,
    .input_count_i,
    .input_addr0_i,
    .input_addr1_i,
    .input_ready_o,
    .input_commit_o,
    .policy_microsteps_o,
    .protocol_error_o(tx_protocol_error),
    .link_valid_o,
    .link_addr0_o,
    .link_addr1_o
  );

  w2_single_edge_pair_rx rx (
    .clk_i,
    .rst_i,
    .link_valid_i(link_valid_o),
    .link_addr0_i(link_addr0_o),
    .link_addr1_i(link_addr1_o),
    .retire_valid_o,
    .retire_addr0_o,
    .retire_addr1_o,
    .protocol_error_o(rx_protocol_error)
  );

  assign protocol_error_o = tx_protocol_error || rx_protocol_error;
  // Drain is deliberately a clean drain.  Sticky protocol failure keeps the
  // endpoint out of the drained state until a sampled reset clears the error.
  assign drain_idle_o = !protocol_error_o && !link_valid_o &&
                        (retire_valid_o == 2'b00);
endmodule
