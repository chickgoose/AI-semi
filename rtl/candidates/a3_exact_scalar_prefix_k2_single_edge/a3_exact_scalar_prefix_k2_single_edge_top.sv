`timescale 1ns/1ps

// Complete A3 endpoint from the pending/accept boundary through a shared-clock
// single-edge TX/RX link to always-ready retirement.  The A3 owner's ordered
// two-step scalar-prefix offer remains atomic at the endpoint boundary.
module a3_exact_scalar_prefix_k2_single_edge_top (
  input  logic        clk_i,
  input  logic        rst_i,
  input  logic        link_enable_i,
  input  logic [15:0] source_pending_i,
  output logic [15:0] source_accept_o,
  output logic [1:0]  accept_count_o,
  output logic [3:0]  accept_addr0_o,
  output logic [3:0]  accept_addr1_o,
  output logic        link_valid_o,
  output logic [3:0]  link_addr0_o,
  output logic [3:0]  link_addr1_o,
  output logic [1:0]  retire_valid_o,
  output logic [3:0]  retire_addr0_o,
  output logic [3:0]  retire_addr1_o,
  output logic        protocol_error_o,
  output logic        drain_idle_o
);
  logic [1:0] scheduler_count;
  logic [3:0] scheduler_addr0;
  logic [3:0] scheduler_addr1;
  logic scheduler_ready;
  logic endpoint_commit;
  logic [1:0] endpoint_microsteps;
  logic endpoint_error;
  logic endpoint_idle;
  logic scheduler_shape_error;
  logic protocol_error_event;

  assign scheduler_shape_error = (scheduler_count == 2'd3) ||
                                 ((scheduler_count == 2'd2) &&
                                  (scheduler_addr0 == scheduler_addr1));

  a3_exact_scalar_prefix_k2 scheduler (
    .clk(clk_i),
    .rst(rst_i),
    .source_pending(source_pending_i),
    .grant_count(scheduler_count),
    .lane0_addr(scheduler_addr0),
    .lane1_addr(scheduler_addr1),
    .bundle_ready(scheduler_ready)
  );

  w2_single_edge_exact_pair_endpoint endpoint (
    .clk_i,
    .rst_i,
    .link_enable_i(link_enable_i && !scheduler_shape_error),
    .input_count_i(scheduler_count),
    .input_addr0_i(scheduler_addr0),
    .input_addr1_i(scheduler_addr1),
    .input_ready_o(scheduler_ready),
    .input_commit_o(endpoint_commit),
    .policy_microsteps_o(endpoint_microsteps),
    .protocol_error_o(endpoint_error),
    .link_valid_o,
    .link_addr0_o,
    .link_addr1_o,
    .retire_valid_o,
    .retire_addr0_o,
    .retire_addr1_o,
    .drain_idle_o(endpoint_idle)
  );

  always_comb begin
    source_accept_o = 16'd0;
    if (endpoint_commit) begin
      source_accept_o[scheduler_addr0] = 1'b1;
      if (scheduler_count == 2'd2)
        source_accept_o[scheduler_addr1] = 1'b1;
    end
  end

  assign accept_count_o = endpoint_commit ? scheduler_count : 2'd0;
  assign accept_addr0_o = endpoint_commit ? scheduler_addr0 : 4'd0;
  assign accept_addr1_o = (endpoint_commit && (scheduler_count == 2'd2)) ?
                          scheduler_addr1 : 4'd0;
  assign protocol_error_event = endpoint_error || scheduler_shape_error ||
                                (endpoint_commit &&
                                 (endpoint_microsteps != scheduler_count));

  // Reset clears this diagnostic history.  Qualification must reject any
  // reset sampled before clean drain; there is no hidden reset-violation bit.
  w2_single_edge_error_latch sticky_error (
    .clk_i,
    .rst_i,
    .error_event_i(protocol_error_event),
    .protocol_error_o
  );

  assign drain_idle_o = (source_pending_i == 16'd0) &&
                        (scheduler_count == 2'd0) && endpoint_idle &&
                        !protocol_error_o;
endmodule
