`timescale 1ns/1ps

// Complete A2 endpoint from the pending/accept boundary through a shared-clock
// single-edge TX/RX link to always-ready retirement.
module a2_batched_iwrr_single_edge_top (
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
  logic [15:0] scheduler_bitmap;
  logic scheduler_idle;
  logic scheduler_ready;
  logic scheduler_commit;
  logic buffer_valid_q;
  logic [1:0] buffer_count_q;
  logic [3:0] buffer_addr0_q;
  logic [3:0] buffer_addr1_q;
  logic endpoint_ready;
  logic endpoint_commit;
  logic [1:0] endpoint_microsteps;
  logic endpoint_error;
  logic endpoint_idle;
  logic scheduler_shape_error;
  logic [15:0] expected_bitmap;

  always_comb begin
    expected_bitmap = 16'd0;
    if (scheduler_count != 2'd0)
      expected_bitmap[scheduler_addr0] = 1'b1;
    if (scheduler_count == 2'd2)
      expected_bitmap[scheduler_addr1] = 1'b1;

    scheduler_shape_error = (scheduler_count == 2'd3) ||
                            ((scheduler_count == 2'd2) &&
                             (scheduler_addr0 == scheduler_addr1)) ||
                            (scheduler_bitmap != expected_bitmap);
  end

  a2_batched_iwrr_k2 scheduler (
    .clk(clk_i),
    .rst(rst_i),
    .req(source_pending_i),
    .grant_count(scheduler_count),
    .grant_addr0(scheduler_addr0),
    .grant_addr1(scheduler_addr1),
    .grant_bitmap(scheduler_bitmap),
    .bundle_ready(scheduler_ready),
    .drain_idle(scheduler_idle)
  );

  // A2's offer is combinational, so one charged register isolates its
  // ready-dependent policy update from endpoint validation/backpressure.
  // The register replaces a consumed record and accepts the next complete
  // scheduler bundle on the same edge; it never admits a partial pair.
  assign scheduler_ready = !rst_i && link_enable_i &&
                           (!buffer_valid_q || endpoint_ready);
  assign scheduler_commit = (scheduler_count != 2'd0) && scheduler_ready;

  always_ff @(posedge clk_i) begin
    if (rst_i) begin
      buffer_valid_q <= 1'b0;
      buffer_count_q <= 2'd0;
      buffer_addr0_q <= 4'd0;
      buffer_addr1_q <= 4'd0;
    end else if (scheduler_ready) begin
      buffer_valid_q <= (scheduler_count != 2'd0);
      buffer_count_q <= scheduler_count;
      buffer_addr0_q <= scheduler_addr0;
      buffer_addr1_q <= scheduler_addr1;
    end
  end

  w2_single_edge_exact_pair_endpoint endpoint (
    .clk_i,
    .rst_i,
    .link_enable_i(link_enable_i),
    .input_count_i(buffer_valid_q ? buffer_count_q : 2'd0),
    .input_addr0_i(buffer_valid_q ? buffer_addr0_q : 4'd0),
    .input_addr1_i(buffer_valid_q ? buffer_addr1_q : 4'd0),
    .input_ready_o(endpoint_ready),
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

  assign accept_count_o = scheduler_commit ? scheduler_count : 2'd0;
  assign accept_addr0_o = scheduler_commit ? scheduler_addr0 : 4'd0;
  assign accept_addr1_o = (scheduler_commit && (scheduler_count == 2'd2)) ?
                          scheduler_addr1 : 4'd0;
  assign source_accept_o = scheduler_commit ? scheduler_bitmap : 16'd0;
  assign protocol_error_o = endpoint_error || scheduler_shape_error ||
                            (endpoint_commit &&
                             (endpoint_microsteps != buffer_count_q));
  assign drain_idle_o = scheduler_idle && !buffer_valid_q && endpoint_idle &&
                        (scheduler_count == 2'd0);
endmodule
