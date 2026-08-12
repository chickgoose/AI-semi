`timescale 1ns/1ps

// Scheduler-facing wrapper for the reusable P6 core.  A bundle commit accepts
// all valid addresses together and advances the upstream policy by exactly
// policy_microsteps_o.  Independent retire-lane stalls are intentionally not
// part of this always-ready link boundary.
module a7_p6_atomic_bundle_adapter (
  input  logic       ref_clk_i,
  input  logic       sample_clk_i,
  input  logic       rst_n,
  input  logic       bundle_valid_i,
  input  logic [1:0] grant_count_i,
  input  logic [3:0] grant_addr0_i,
  input  logic [3:0] grant_addr1_i,
  output logic       bundle_ready_o,
  output logic       bundle_commit_o,
  output logic [1:0] policy_microsteps_o,
  output logic       bundle_protocol_error_o,
  output logic       p6_clk_o,
  output logic [4:0] p6_data_o,
  output logic [1:0] retire_valid_o,
  output logic [3:0] retire_addr0_o,
  output logic [3:0] retire_addr1_o,
  output logic       retire_protocol_error_o,
  output logic       drain_idle_o
);
  logic endpoint_valid;
  logic [1:0] endpoint_count;
  logic [3:0] endpoint_addr0;
  logic [3:0] endpoint_addr1;
  logic endpoint_ready;
  logic endpoint_input_error;

  a7_p6_atomic_bundle_frontend frontend (
    .bundle_valid_i, .grant_count_i, .grant_addr0_i, .grant_addr1_i,
    .endpoint_ready_i(endpoint_ready),
    .endpoint_protocol_error_i(endpoint_input_error),
    .bundle_ready_o, .bundle_commit_o, .policy_microsteps_o,
    .bundle_protocol_error_o, .endpoint_valid_o(endpoint_valid),
    .endpoint_count_o(endpoint_count), .endpoint_addr0_o(endpoint_addr0),
    .endpoint_addr1_o(endpoint_addr1)
  );

  a7_p6_exact_pair_endpoint endpoint (
    .ref_clk_i, .sample_clk_i, .rst_n,
    .input_valid_i(endpoint_valid), .input_count_i(endpoint_count),
    .input_addr0_i(endpoint_addr0), .input_addr1_i(endpoint_addr1),
    .input_ready_o(endpoint_ready),
    .input_protocol_error_o(endpoint_input_error),
    .p6_clk_o, .p6_data_o, .retire_valid_o, .retire_addr0_o,
    .retire_addr1_o, .retire_protocol_error_o, .drain_idle_o
  );
endmodule
