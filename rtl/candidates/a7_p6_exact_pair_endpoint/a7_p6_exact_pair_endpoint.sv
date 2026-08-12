`timescale 1ns/1ps

// Reusable scheduler-neutral P6 endpoint for exactly one atomic transaction
// per scheduler cycle.  The transaction contains zero (valid low), one, or
// two ordered four-bit addresses.  There is deliberately no aggregation wait,
// prediction, scheduler policy, or queue in this endpoint.
module a7_p6_exact_pair_endpoint (
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
  logic launch_fire;
  logic frame_active;
  logic [1:0] raw_count;
  logic [3:0] raw_addr0;
  logic [3:0] raw_addr1;
  logic raw_toggle;
  logic raw_protocol_error;
  logic seen_toggle;

  a7_p6_pair_launch launch (
    .ref_clk_i, .rst_n, .input_valid_i, .input_count_i,
    .input_ready_o, .launch_fire_o(launch_fire),
    .input_protocol_error_o
  );

  a7_p6_pair_tx tx (
    .ref_clk_i, .sample_clk_i, .rst_n,
    .launch_fire_i(launch_fire), .input_count_i,
    .input_addr0_i, .input_addr1_i,
    .frame_active_o(frame_active), .p6_clk_o, .p6_data_o
  );

  a7_p6_pair_rx rx (
    .rst_n, .p6_clk_i(p6_clk_o), .p6_data_i(p6_data_o),
    .raw_count_o(raw_count), .raw_addr0_o(raw_addr0),
    .raw_addr1_o(raw_addr1), .raw_toggle_o(raw_toggle),
    .raw_protocol_error_o(raw_protocol_error)
  );

  a7_p6_pair_observer observer (
    .ref_clk_i, .rst_n, .raw_count_i(raw_count),
    .raw_addr0_i(raw_addr0), .raw_addr1_i(raw_addr1),
    .raw_toggle_i(raw_toggle),
    .raw_protocol_error_i(raw_protocol_error),
    .retire_valid_o, .retire_addr0_o, .retire_addr1_o,
    .retire_protocol_error_o, .seen_toggle_o(seen_toggle)
  );

  assign drain_idle_o = !launch_fire && !frame_active && !p6_clk_o &&
                        !(raw_toggle ^ seen_toggle) &&
                        (retire_valid_o == 2'b00);
endmodule
