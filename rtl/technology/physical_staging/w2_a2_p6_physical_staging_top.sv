`timescale 1ns/1ps

// Complete A2 scheduler + charged elastic record + technology P6 endpoint.
// Debug stays internal; this is the normalized final P&R boundary.
module w2_a2_p6_physical_staging_top (
  input  logic        ref_clk_i,
  input  logic        sample_clk_i,
  input  logic        rst_n,
  input  logic [15:0] source_pending_i,
  output logic [15:0] source_accept_o,
  output logic        link_clk_o,
  output logic [4:0]  link_data_o,
  output logic [1:0]  retire_valid_o,
  output logic [3:0]  retire_addr0_o,
  output logic [3:0]  retire_addr1_o,
  output logic        drain_idle_o,
  output logic        protocol_error_o
);
  logic scheduler_idle, scheduler_ready;
  logic [1:0] grant_count;
  logic [3:0] grant_addr0, grant_addr1;
  logic [15:0] grant_bitmap;
  logic grant_commit;
  logic buffer_valid_q;
  logic [1:0] buffer_count_q;
  logic [3:0] buffer_addr0_q, buffer_addr1_q;
  logic adapter_ready, link_commit;
  logic [1:0] link_microsteps;
  logic link_input_error, link_retire_error, link_idle;

  assign scheduler_ready = !buffer_valid_q || adapter_ready;
  assign grant_commit = rst_n && (grant_count != 2'd0) && scheduler_ready;
  assign source_accept_o = grant_commit ? grant_bitmap : 16'd0;

  a2_batched_iwrr_k2 scheduler (
    .clk(ref_clk_i), .rst(!rst_n), .req(source_pending_i),
    .grant_count, .grant_addr0, .grant_addr1, .grant_bitmap,
    .bundle_ready(scheduler_ready), .drain_idle(scheduler_idle)
  );

  always_ff @(posedge ref_clk_i or negedge rst_n) begin
    if (!rst_n) begin
      buffer_valid_q <= 1'b0;
      buffer_count_q <= 2'd0;
      buffer_addr0_q <= 4'd0;
      buffer_addr1_q <= 4'd0;
    end else if (scheduler_ready) begin
      buffer_valid_q <= (grant_count != 2'd0);
      buffer_count_q <= grant_count;
      buffer_addr0_q <= grant_addr0;
      buffer_addr1_q <= grant_addr1;
    end
  end

  w2_p6_atomic_bundle_adapter_tech link (
    .ref_clk_i, .sample_clk_i, .rst_n,
    .bundle_valid_i(buffer_valid_q),
    .grant_count_i(buffer_valid_q ? buffer_count_q : 2'd0),
    .grant_addr0_i(buffer_addr0_q), .grant_addr1_i(buffer_addr1_q),
    .bundle_ready_o(adapter_ready), .bundle_commit_o(link_commit),
    .policy_microsteps_o(link_microsteps),
    .bundle_protocol_error_o(link_input_error),
    .p6_clk_o(link_clk_o), .p6_data_o(link_data_o),
    .retire_valid_o, .retire_addr0_o, .retire_addr1_o,
    .retire_protocol_error_o(link_retire_error), .drain_idle_o(link_idle)
  );

  assign protocol_error_o = link_input_error || link_retire_error ||
                            (link_commit && (link_microsteps != buffer_count_q));
  assign drain_idle_o = rst_n && !(|source_pending_i) && scheduler_idle &&
                        !buffer_valid_q && link_idle && !(|retire_valid_o) &&
                        !protocol_error_o;
endmodule
