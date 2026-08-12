`timescale 1ns/1ps

// A8 observation shim. It adds no behavior in mode 0. Modes 1..6 are
// deliberately faulty observations used to prove the independent monitor.
module a8_k2_blackbox_adapter (
  input  logic        clk,
  input  logic        rst,
  input  logic [15:0] req,
  input  logic        bundle_ready,
  input  integer      mutation_mode,
  output logic [1:0]  grant_count,
  output logic [3:0]  grant_addr0,
  output logic [3:0]  grant_addr1,
  output logic [15:0] accept_mask,
  output logic        drain_idle
);
  logic [1:0] raw_count;
  logic [3:0] raw_addr0, raw_addr1;
  logic [15:0] raw_accept;
  logic raw_drain;
  logic [3:0] prior_addr_q;
  logic prior_valid_q;

`ifdef A8_OWNER_A2
  logic [15:0] owner_bitmap;
  a2_batched_iwrr_k2 owner (
    .clk(clk), .rst(rst), .req(req),
    .grant_count(raw_count), .grant_addr0(raw_addr0),
    .grant_addr1(raw_addr1), .grant_bitmap(owner_bitmap),
    .bundle_ready(bundle_ready), .drain_idle(raw_drain)
  );
  always_comb raw_accept = (!rst && bundle_ready) ? owner_bitmap : 16'b0;
`elsif A8_OWNER_A3
  logic [15:0] source_event [16];
  logic [15:0] source_ready;
  logic [1:0] retire_valid;
  logic [1:0] retire_ready;
  logic [15:0] retire_event [2];
  logic [3:0] retire_source [2];
  integer source_index;
  always_comb begin
    for (source_index = 0; source_index < 16; source_index = source_index + 1)
      source_event[source_index] = 16'ha500 + source_index;
    retire_ready = {2{bundle_ready}};
    raw_count = retire_valid[1] ? 2'd2 : (retire_valid[0] ? 2'd1 : 2'd0);
    raw_addr0 = retire_source[0];
    raw_addr1 = retire_source[1];
    raw_accept = source_ready;
    // The emerging zero-feature wrapper has no native drain output. This is
    // only a black-box quiescence observation, never a claimed owner signal.
    raw_drain = (req == 0) && (retire_valid == 0);
  end
  a3_k2_zero_feature_wrapper owner_wrapper (
    .clk(clk), .rst_n(~rst), .source_valid(req), .source_event(source_event),
    .source_ready(source_ready), .retire_valid(retire_valid),
    .retire_ready(retire_ready), .retire_event(retire_event),
    .retire_source(retire_source)
  );
`else
  initial $fatal(1, "A8 owner selection macro missing");
`endif

  always_ff @(posedge clk) begin
    if (rst && mutation_mode != 6) begin
      prior_addr_q <= 0;
      prior_valid_q <= 0;
    end else if (!rst && raw_count != 0 && bundle_ready) begin
      prior_addr_q <= raw_addr0;
      prior_valid_q <= 1;
    end
  end

  always_comb begin
    grant_count = raw_count;
    grant_addr0 = raw_addr0;
    grant_addr1 = raw_addr1;
    accept_mask = raw_accept;
    drain_idle = raw_drain;
    case (mutation_mode)
      1: if (raw_count == 2) begin
        grant_addr0 = raw_addr1;
        grant_addr1 = raw_addr0;
      end
      2: if (raw_count == 2) grant_addr1 = raw_addr0;
      3: if (raw_count == 2) accept_mask[raw_addr1] = 1'b0;
      4: drain_idle = 1'b1;
      5: if (prior_valid_q && raw_count != 0) grant_addr0 = prior_addr_q;
      6: if (rst && prior_valid_q) begin
        grant_count = 1;
        grant_addr0 = prior_addr_q;
      end
      default: begin end
    endcase
  end
endmodule
