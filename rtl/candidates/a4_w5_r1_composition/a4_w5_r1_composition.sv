`timescale 1ns/1ps

// Independent A4 composition shell around the exact pinned A7 W5 production
// endpoint and its charged parallel reference. No queue or CDC state is added.
module a4_w5_r1_composition (
  input  logic       ref_clk_i,
  input  logic       sample_clk_i,
  input  logic       rst_n,
  input  logic       producer_valid_i,
  input  logic [3:0] producer_addr_i,
  output logic       producer_ready_o,
  output logic       accepted_o,
  output logic       burst_clk_o,
  output logic [1:0] burst_data_o,
  output logic [3:0] serial_retire_addr_o,
  output logic       serial_retire_valid_o,
  output logic       serial_drain_idle_o,
  output logic       parallel_strobe_o,
  output logic [3:0] parallel_link_data_o,
  output logic [3:0] parallel_retire_addr_o,
  output logic       parallel_retire_valid_o,
  output logic       parallel_drain_idle_o
);
  logic serial_ready;
  logic parallel_ready;
  logic joint_valid;

  // Both production endpoints have the same charged reset-release qualifier.
  // Gating valid with the joint readiness makes divergence fail closed while
  // preserving one accepted event on every ready-valid ref edge at R1.
  assign producer_ready_o = serial_ready & parallel_ready;
  assign joint_valid = producer_valid_i & producer_ready_o;
  assign accepted_o = joint_valid;

  a7_r1_candidate_endpoint serial_endpoint (
    .ref_clk_i, .sample_clk_i, .rst_n,
    .event_valid_i(joint_valid), .event_addr_i(producer_addr_i),
    .event_ready_o(serial_ready), .burst_clk_o, .burst_data_o,
    .retire_addr_o(serial_retire_addr_o),
    .retire_valid_o(serial_retire_valid_o),
    .drain_idle_o(serial_drain_idle_o)
  );

  a7_r1_parallel_reference_top parallel_endpoint (
    .ref_clk_i, .sample_clk_i, .rst_n,
    .event_valid_i(joint_valid), .event_addr_i(producer_addr_i),
    .event_ready_o(parallel_ready), .link_strobe_o(parallel_strobe_o),
    .link_data_o(parallel_link_data_o),
    .retire_addr_o(parallel_retire_addr_o),
    .retire_valid_o(parallel_retire_valid_o),
    .drain_idle_o(parallel_drain_idle_o)
  );

endmodule
