`timescale 1ns/1ps

// A2 owner scheduler plus a charged one-entry elastic bundle buffer and the
// frozen P6 exact-pair endpoint.  Only complete ordered K2 bundles cross either
// handshake.  The buffer is the sole integration storage (11 state bits).
module a2_batched_iwrr_p6_top (
  input  logic        ref_clk_i,
  input  logic        sample_clk_i,
  input  logic        rst_n,
  input  logic        link_enable_i,
  input  logic [15:0] req_i,
  output logic        grant_commit_o,
  output logic [1:0]  grant_count_o,
  output logic [3:0]  grant_addr0_o,
  output logic [3:0]  grant_addr1_o,
  output logic [15:0] grant_bitmap_o,
  output logic        p6_clk_o,
  output logic [4:0]  p6_data_o,
  output logic [1:0]  retire_valid_o,
  output logic [3:0]  retire_addr0_o,
  output logic [3:0]  retire_addr1_o,
  output logic        protocol_error_o,
  output logic        drain_idle_o
);
  logic scheduler_idle;
  logic scheduler_ready;

  logic        buffer_valid_q;
  logic [1:0]  buffer_count_q;
  logic [3:0]  buffer_addr0_q;
  logic [3:0]  buffer_addr1_q;

  logic adapter_ready;
  logic link_ready;
  logic link_commit;
  logic [1:0] link_microsteps;
  logic link_input_error;
  logic link_retire_error;
  logic link_idle;

  // The registered buffer payload is the complete endpoint input.  Ready can
  // affect scheduler admission, but no endpoint output depends combinationally
  // on the scheduler's current offer, so this elastic path contains no loop.
  assign link_ready = link_enable_i && adapter_ready;
  assign scheduler_ready = !buffer_valid_q || link_ready;
  assign grant_commit_o = (grant_count_o != 2'd0) && scheduler_ready && rst_n;

  a2_batched_iwrr_k2 scheduler (
    .clk(ref_clk_i),
    .rst(!rst_n),
    .req(req_i),
    .grant_count(grant_count_o),
    .grant_addr0(grant_addr0_o),
    .grant_addr1(grant_addr1_o),
    .grant_bitmap(grant_bitmap_o),
    .bundle_ready(scheduler_ready),
    .drain_idle(scheduler_idle)
  );

  always_ff @(posedge ref_clk_i or negedge rst_n) begin
    if (!rst_n) begin
      buffer_valid_q <= 1'b0;
      buffer_count_q <= 2'd0;
      buffer_addr0_q <= 4'd0;
      buffer_addr1_q <= 4'd0;
    end else if (scheduler_ready) begin
      buffer_valid_q <= (grant_count_o != 2'd0);
      buffer_count_q <= grant_count_o;
      buffer_addr0_q <= grant_addr0_o;
      buffer_addr1_q <= grant_addr1_o;
    end
  end

  a7_p6_atomic_bundle_adapter p6_adapter (
    .ref_clk_i,
    .sample_clk_i,
    .rst_n,
    .bundle_valid_i(buffer_valid_q && link_enable_i),
    .grant_count_i((buffer_valid_q && link_enable_i) ?
                   buffer_count_q : 2'd0),
    .grant_addr0_i(buffer_addr0_q),
    .grant_addr1_i(buffer_addr1_q),
    .bundle_ready_o(adapter_ready),
    .bundle_commit_o(link_commit),
    .policy_microsteps_o(link_microsteps),
    .bundle_protocol_error_o(link_input_error),
    .p6_clk_o,
    .p6_data_o,
    .retire_valid_o,
    .retire_addr0_o,
    .retire_addr1_o,
    .retire_protocol_error_o(link_retire_error),
    .drain_idle_o(link_idle)
  );

  assign protocol_error_o = link_input_error || link_retire_error ||
                            (link_commit &&
                             (link_microsteps != buffer_count_q));
  assign drain_idle_o = scheduler_idle && !buffer_valid_q && link_idle;
endmodule
