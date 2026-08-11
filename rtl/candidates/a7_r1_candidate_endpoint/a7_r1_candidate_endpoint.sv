`timescale 1ns/1ps

module a7_r1_candidate_endpoint (
  input  logic       ref_clk_i,
  input  logic       sample_clk_i,
  input  logic       rst_n,
  input  logic       event_valid_i,
  input  logic [3:0] event_addr_i,
  output logic       event_ready_o,
  output logic       burst_clk_o,
  output logic [1:0] burst_data_o,
  output logic [3:0] retire_addr_o,
  output logic       retire_valid_o,
  output logic       drain_idle_o
);
  logic launch_fire;
  logic frame_active;
  logic [3:0] raw_retire_addr;
  logic raw_retire_toggle;
  logic seen_retire_toggle;

  a7_r1_launch_qualifier launch_qualifier (
    .ref_clk_i(ref_clk_i), .rst_n(rst_n), .event_valid_i(event_valid_i),
    .event_ready_o(event_ready_o), .launch_fire_o(launch_fire));

  a7_r1_ddr_tx tx (
    .ref_clk_i(ref_clk_i), .sample_clk_i(sample_clk_i), .rst_n(rst_n),
    .launch_fire_i(launch_fire), .event_addr_i(event_addr_i),
    .frame_active_o(frame_active), .burst_clk_o(burst_clk_o),
    .burst_data_o(burst_data_o));

  a7_r1_ddr_rx rx (
    .rst_n(rst_n), .burst_clk_i(burst_clk_o), .burst_data_i(burst_data_o),
    .retire_addr_o(raw_retire_addr), .retire_toggle_o(raw_retire_toggle));

  a7_r1_retire_observer retire_observer (
    .ref_clk_i(ref_clk_i), .rst_n(rst_n), .raw_addr_i(raw_retire_addr),
    .raw_toggle_i(raw_retire_toggle), .retire_addr_o(retire_addr_o),
    .retire_valid_o(retire_valid_o), .seen_toggle_o(seen_retire_toggle));

  // Idle means no admission, frame, unobserved raw commit, or registered
  // output remains to be sampled by the always-ready ref-domain consumer.
  assign drain_idle_o = ~launch_fire & ~frame_active & ~burst_clk_o &
                        ~(raw_retire_toggle ^ seen_retire_toggle) &
                        ~retire_valid_o;
endmodule
