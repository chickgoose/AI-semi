`timescale 1ns/1ps

`ifndef K2_EXPECT_LATENCY
`define K2_EXPECT_LATENCY 0
`endif

// Test-only neutral scheduler used to qualify the harness.  It is not a
// promotion candidate and deliberately uses lowest-address-first policy.
module k2_reference_candidate #(
  parameter integer OFFER_LATENCY = `K2_EXPECT_LATENCY
) (
  input  logic        clk,
  input  logic        rst,
  input  logic [15:0] source_pending,
  output logic [1:0]  grant_count,
  output logic [3:0]  grant_addr0,
  output logic [3:0]  grant_addr1,
  input  logic        bundle_ready
);
  logic [1:0] fresh_count;
  logic [3:0] fresh_addr0, fresh_addr1;
  logic [1:0] count_q;
  logic [3:0] addr0_q, addr1_q;
  logic [15:0] selection_pending;
  logic [15:0] accepted_mask;
  logic hold_toggle_q;
  logic bubble_q;
  integer index;

  always @* begin
    accepted_mask = 0;
    if ((count_q != 0) && bundle_ready) begin
      accepted_mask[addr0_q] = 1'b1;
      if (count_q == 2)
        accepted_mask[addr1_q] = 1'b1;
    end
    selection_pending = source_pending;
    if (OFFER_LATENCY != 0)
      selection_pending = source_pending & ~accepted_mask;

    fresh_count = 0;
    fresh_addr0 = 0;
    fresh_addr1 = 0;
    for (index = 0; index < 16; index = index + 1) begin
      if (selection_pending[index] && (fresh_count < 2)) begin
        if (fresh_count == 0)
          fresh_addr0 = index[3:0];
        else
          fresh_addr1 = index[3:0];
        fresh_count = fresh_count + 1'b1;
      end
    end
  end

  always @* begin
    if (OFFER_LATENCY == 0) begin
      if (count_q != 0) begin
        grant_count = count_q;
        grant_addr0 = addr0_q;
        grant_addr1 = addr1_q;
      end else begin
        grant_count = fresh_count;
        grant_addr0 = fresh_addr0;
        grant_addr1 = fresh_addr1;
      end
    end else begin
      grant_count = count_q;
      grant_addr0 = addr0_q;
      grant_addr1 = addr1_q;
    end

`ifdef K2_MUT_BAD_COUNT0
    if (source_pending == 0) begin
      grant_count = 1;
      grant_addr0 = 0;
    end
`endif
`ifdef K2_MUT_BAD_COUNT1
    if (grant_count == 1) begin
      grant_count = 2;
      grant_addr1 = grant_addr0;
    end
`endif
`ifdef K2_MUT_BAD_COUNT2
    if (grant_count == 2)
      grant_count = 1;
`endif
`ifdef K2_MUT_DUPLICATE
    if (grant_count == 2)
      grant_addr1 = grant_addr0;
`endif
`ifdef K2_MUT_PHANTOM
    if (grant_count != 0)
      grant_addr0 = grant_addr0 ^ 4'h8;
`endif
`ifdef K2_MUT_HELD_REORDER
    if ((grant_count == 2) && hold_toggle_q) begin
      grant_addr0 = addr1_q;
      grant_addr1 = addr0_q;
    end
`endif
`ifdef K2_MUT_REFILL_BUBBLE
    if (bubble_q)
      grant_count = 0;
`endif
    if (rst) begin
      grant_count = 0;
      grant_addr0 = 0;
      grant_addr1 = 0;
    end
  end

  always @(posedge clk) begin
    hold_toggle_q <= ~hold_toggle_q;
    bubble_q <= 1'b0;
    if (rst) begin
`ifndef K2_MUT_RESET_STALE
      count_q <= 0;
      addr0_q <= 0;
      addr1_q <= 0;
`endif
      hold_toggle_q <= 0;
    end else if (OFFER_LATENCY == 0) begin
      if (count_q != 0) begin
        if (bundle_ready)
          count_q <= 0;
`ifdef K2_MUT_PARTIAL_COUNT2
        else if (count_q == 2)
          count_q <= 1;
`endif
      end else if ((fresh_count != 0) && !bundle_ready) begin
        count_q <= fresh_count;
        addr0_q <= fresh_addr0;
        addr1_q <= fresh_addr1;
      end
    end else begin
      if ((count_q == 0) || bundle_ready) begin
        count_q <= fresh_count;
        addr0_q <= fresh_addr0;
        addr1_q <= fresh_addr1;
`ifdef K2_MUT_REFILL_BUBBLE
        if ((count_q != 0) && bundle_ready)
          bubble_q <= 1'b1;
`endif
      end
`ifdef K2_MUT_PARTIAL_COUNT2
      else if (count_q == 2)
        count_q <= 1;
`endif
    end
  end
endmodule

module k2_candidate_binding (
  input  logic        clk,
  input  logic        rst,
  input  logic [15:0] source_pending,
  output logic [1:0]  grant_count,
  output logic [3:0]  grant_addr0,
  output logic [3:0]  grant_addr1,
  input  logic        bundle_ready,
  output logic        drain_idle
);
`ifdef K2_MUT_LATENCY
  localparam integer ACTUAL_LATENCY = (`K2_EXPECT_LATENCY == 0) ? 1 : 0;
`else
  localparam integer ACTUAL_LATENCY = `K2_EXPECT_LATENCY;
`endif

  k2_reference_candidate #(.OFFER_LATENCY(ACTUAL_LATENCY)) reference (.*);

  always @* begin
    drain_idle = (source_pending == 0) && (grant_count == 0);
  end
endmodule
