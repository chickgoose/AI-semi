`timescale 1ns/1ps

// Exact two-address prefix of the canonical N=16 scalar Fovea policy.
//
// The output is one atomic ordered bundle.  grant_count is 0, 1, or 2; lane0
// is g0 when count>=1, and lane1 is g1 when count==2.  g1 is the canonical
// scalar winner after g0's address is masked and every canonical policy/RR
// state transition caused by g0 is applied.  There is no independent lane
// ready: bundle_ready commits the whole offer and exactly grant_count scalar
// policy microsteps together.
//
// source_pending is a level-held, one-outstanding-per-address bitmap.  Once
// selected, an address is reserved by the registered bundle and may clear from
// source_pending; new unrelated bits may be added during a stall.  A same-
// address clear/retrigger on one edge is not representable by this boundary.
module a3_exact_scalar_prefix_k2 (
  input  logic        clk,
  input  logic        rst,
  input  logic [15:0] source_pending,

  output logic [1:0]  grant_count,
  output logic [3:0]  lane0_addr,
  output logic [3:0]  lane1_addr,
  input  logic        bundle_ready
);
  localparam logic [3:0] CENTER_MASK = 4'b0110;
  localparam logic [3:0] PERIPH_MASK = 4'b1001;

  // Each arbiter4_tree has {top, hi_pair, lo_pair} last-grant state.
  logic [2:0] center_state;
  logic [2:0] periph_state;
  logic [2:0] column_state;
  logic [2:0] round_state;

  logic [2:0] bundle_post_center;
  logic [2:0] bundle_post_periph;
  logic [2:0] bundle_post_column;
  logic [2:0] bundle_post_round;

  logic [15:0] buffered_grant_mask;
  logic [15:0] selection_req;
  logic [2:0] selection_center;
  logic [2:0] selection_periph;
  logic [2:0] selection_column;
  logic [2:0] selection_round;

  logic [32:0] stage0;
  logic [32:0] stage1;
  logic [15:0] stage1_req;
  logic candidate0_valid;
  logic [3:0] candidate0_addr;
  logic candidate1_valid;
  logic [3:0] candidate1_addr;
  logic [2:0] candidate_post_center;
  logic [2:0] candidate_post_periph;
  logic [2:0] candidate_post_column;
  logic [2:0] candidate_post_round;

  wire bundle_fire = (grant_count != 2'd0) && bundle_ready;

  function automatic logic [1:0] arb2_grant(
    input logic [1:0] req,
    input logic       last_grant
  );
    logic prefer1;
    logic grant1;
    logic grant0;
    begin
      prefer1 = (last_grant == 1'b0);
      grant1 = req[1] & (prefer1 | ~req[0]);
      grant0 = req[0] & ~grant1;
      arb2_grant = {grant1, grant0};
    end
  endfunction

  function automatic logic arb2_next(
    input logic [1:0] req,
    input logic       last_grant
  );
    logic [1:0] grant;
    begin
      grant = arb2_grant(req, last_grant);
      arb2_next = (|req) ? grant[1] : last_grant;
    end
  endfunction

  function automatic logic [3:0] arb4_grant(
    input logic [3:0] req,
    input logic [2:0] state
  );
    logic [1:0] lo_grant;
    logic [1:0] hi_grant;
    logic [1:0] group_req;
    logic [1:0] group_grant;
    begin
      lo_grant = arb2_grant(req[1:0], state[0]);
      hi_grant = arb2_grant(req[3:2], state[1]);
      group_req = {|req[3:2], |req[1:0]};
      group_grant = arb2_grant(group_req, state[2]);
      arb4_grant[1:0] = group_grant[0] ? lo_grant : 2'b00;
      arb4_grant[3:2] = group_grant[1] ? hi_grant : 2'b00;
    end
  endfunction

  function automatic logic [2:0] arb4_next(
    input logic [3:0] req,
    input logic [2:0] state
  );
    logic [1:0] group_req;
    begin
      group_req = {|req[3:2], |req[1:0]};
      arb4_next[0] = arb2_next(req[1:0], state[0]);
      arb4_next[1] = arb2_next(req[3:2], state[1]);
      arb4_next[2] = arb2_next(group_req, state[2]);
    end
  endfunction

  function automatic logic [1:0] idx4(input logic [3:0] bits);
    begin
      if (bits[0])
        idx4 = 2'd0;
      else if (bits[1])
        idx4 = 2'd1;
      else if (bits[2])
        idx4 = 2'd2;
      else
        idx4 = 2'd3;
    end
  endfunction

  // Packed result:
  // {grant_onehot, valid, addr, next_round, next_center, next_periph,
  //  next_column}
  function automatic logic [32:0] scalar_grant(
    input logic [15:0] req,
    input logic [2:0]  round_in,
    input logic [2:0]  center_in,
    input logic [2:0]  periph_in,
    input logic [2:0]  column_in
  );
    logic [3:0] row_req;
    logic center_available;
    logic periph_available;
    logic prefer_center;
    logic use_center;
    logic use_periph;
    logic [3:0] center_req;
    logic [3:0] periph_req;
    logic [3:0] center_grant;
    logic [3:0] periph_grant;
    logic [3:0] row_grant;
    logic [1:0] row_index;
    logic [3:0] selected_columns;
    logic [3:0] column_grant;
    logic [1:0] column_index;
    logic [15:0] grant_onehot;
    logic valid;
    logic [2:0] round_out;
    logic [2:0] center_out;
    logic [2:0] periph_out;
    logic [2:0] column_out;
    begin
      row_req[0] = |req[3:0];
      row_req[1] = |req[7:4];
      row_req[2] = |req[11:8];
      row_req[3] = |req[15:12];

      center_available = |(row_req & CENTER_MASK);
      periph_available = |(row_req & PERIPH_MASK);
      prefer_center = (round_in != 3'd5);
      use_center = (prefer_center && center_available) ||
                   (!prefer_center && !periph_available && center_available);
      use_periph = (!prefer_center && periph_available) ||
                   (prefer_center && !center_available && periph_available);

      center_req = use_center ? (row_req & CENTER_MASK) : 4'b0000;
      periph_req = use_periph ? (row_req & PERIPH_MASK) : 4'b0000;
      center_grant = arb4_grant(center_req, center_in);
      periph_grant = arb4_grant(periph_req, periph_in);
      row_grant = use_center ? center_grant :
                  (use_periph ? periph_grant : 4'b0000);
      valid = |row_grant;
      row_index = idx4(row_grant);

      case (row_index)
        2'd0: selected_columns = req[3:0];
        2'd1: selected_columns = req[7:4];
        2'd2: selected_columns = req[11:8];
        default: selected_columns = req[15:12];
      endcase
      if (!valid)
        selected_columns = 4'b0000;

      column_grant = arb4_grant(selected_columns, column_in);
      column_index = idx4(column_grant);
      grant_onehot[3:0] = {4{row_grant[0]}} & column_grant;
      grant_onehot[7:4] = {4{row_grant[1]}} & column_grant;
      grant_onehot[11:8] = {4{row_grant[2]}} & column_grant;
      grant_onehot[15:12] = {4{row_grant[3]}} & column_grant;

      center_out = arb4_next(center_req, center_in);
      periph_out = arb4_next(periph_req, periph_in);
      column_out = arb4_next(selected_columns, column_in);
      if (valid)
        round_out = (round_in == 3'd5) ? 3'd0 : round_in + 1'b1;
      else
        round_out = round_in;

      scalar_grant = {grant_onehot, valid, {row_index, column_index},
                      round_out, center_out, periph_out, column_out};
    end
  endfunction

  always @* begin
    buffered_grant_mask = 16'b0;
    if (grant_count >= 2'd1)
      buffered_grant_mask[lane0_addr] = 1'b1;
    if (grant_count == 2'd2)
      buffered_grant_mask[lane1_addr] = 1'b1;

    if (bundle_fire) begin
      selection_req = source_pending & ~buffered_grant_mask;
      selection_round = bundle_post_round;
      selection_center = bundle_post_center;
      selection_periph = bundle_post_periph;
      selection_column = bundle_post_column;
    end else begin
      selection_req = source_pending;
      selection_round = round_state;
      selection_center = center_state;
      selection_periph = periph_state;
      selection_column = column_state;
    end

    stage0 = scalar_grant(selection_req, selection_round,
                          selection_center, selection_periph,
                          selection_column);
    candidate0_valid = stage0[16];
    candidate0_addr = stage0[15:12];
    stage1_req = selection_req;
    if (candidate0_valid)
      stage1_req = selection_req & ~stage0[32:17];

`ifdef A3_K2_MUT_DUP
    stage1_req = selection_req;
`endif

`ifdef A3_K2_MUT_STATE_ADV
    stage1 = scalar_grant(stage1_req, selection_round,
                          selection_center, selection_periph,
                          selection_column);
`else
    stage1 = scalar_grant(stage1_req, stage0[11:9], stage0[8:6],
                          stage0[5:3], stage0[2:0]);
`endif
    candidate1_valid = candidate0_valid && stage1[16];
    candidate1_addr = stage1[15:12];

    if (candidate1_valid) begin
      candidate_post_round = stage1[11:9];
      candidate_post_center = stage1[8:6];
      candidate_post_periph = stage1[5:3];
      candidate_post_column = stage1[2:0];
    end else begin
      candidate_post_round = stage0[11:9];
      candidate_post_center = stage0[8:6];
      candidate_post_periph = stage0[5:3];
      candidate_post_column = stage0[2:0];
    end
  end

  always @(posedge clk) begin
    if (rst) begin
      // Canonical arbiter2 reset state is last_grant=1 in every tree node.
      center_state <= 3'b111;
      periph_state <= 3'b111;
      column_state <= 3'b111;
      round_state <= 3'd0;
      grant_count <= 2'd0;
      lane0_addr <= 4'd0;
      lane1_addr <= 4'd0;
      bundle_post_center <= 3'b111;
      bundle_post_periph <= 3'b111;
      bundle_post_column <= 3'b111;
      bundle_post_round <= 3'd0;
    end else if ((grant_count == 2'd0) || bundle_ready) begin
      if (bundle_fire) begin
        center_state <= bundle_post_center;
        periph_state <= bundle_post_periph;
        column_state <= bundle_post_column;
        round_state <= bundle_post_round;
      end

      if (candidate0_valid) begin
        grant_count <= candidate1_valid ? 2'd2 : 2'd1;
        lane0_addr <= candidate0_addr;
        lane1_addr <= candidate1_valid ? candidate1_addr : 4'd0;
        bundle_post_center <= candidate_post_center;
        bundle_post_periph <= candidate_post_periph;
        bundle_post_column <= candidate_post_column;
        bundle_post_round <= candidate_post_round;
      end else begin
`ifdef A3_K2_MUT_STALE
        grant_count <= grant_count;
`else
        grant_count <= 2'd0;
        lane0_addr <= 4'd0;
        lane1_addr <= 4'd0;
`endif
      end
    end
  end

  // These are elaboration-time contract guards for simulation/formal-capable
  // tools.  The functional datapath does not depend on them.
`ifndef SYNTHESIS
  always @(posedge clk) begin
    if (!rst) begin
      if (grant_count > 2'd2)
        $fatal(1, "A3_K2 illegal grant_count=%0d", grant_count);
      if (grant_count == 2'd2 && (lane0_addr == lane1_addr))
        $fatal(1, "A3_K2 duplicate ordered grants addr=%0d", lane0_addr);
    end
  end
`endif
endmodule
