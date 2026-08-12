`timescale 1ns/1ps

// Paired Cortical Column K2 (PCC-K2), fixed N=16.
//
// The scheduler boundary is one atomic ordered bundle.  grant_count encodes
// zero, one, or two addresses; source_ready asserts for every member only when
// bundle_ready commits the entire bundle.  No lane commits independently.
// A blocked offer is frozen by hold_requests_q and policy state does not move.
//
// The six-phase committed token sequence is (row1,row2) five times followed
// by (row0,row3) once.  Four independent four-column rotating selectors keep
// arbitration local to each row.  A missing scheduled row can lend its token
// to a fallback row and accrues bounded debt.  Debt is served first.  At debt
// saturation fallback stays work-conserving but the token stops, so weight
// debt cannot wrap or silently disappear.
module a4_paired_cortical_column_k2 #(
  parameter int DEBT_WIDTH = 4
) (
  input  logic        clk,
  input  logic        rst_n,
  input  logic [15:0] source_valid,
  output logic [15:0] source_ready,
  output logic [1:0]  grant_count,
  output logic [7:0]  grant_addr,
  input  logic        bundle_ready,
  output logic        drain_idle
);
  localparam logic [DEBT_WIDTH-1:0] DEBT_MAX = {DEBT_WIDTH{1'b1}};

  logic [2:0] phase_q, phase_n;
  logic       token_q, token_n;
  logic [1:0] column_q [0:3];
  logic [1:0] column_n [0:3];
  logic [DEBT_WIDTH-1:0] debt_q [0:3];
  logic [DEBT_WIDTH-1:0] debt_n [0:3];
  logic [1:0] debt_rr_q, debt_rr_n;
  logic [1:0] fallback_rr_q, fallback_rr_n;

  // Boundary elasticity only.  This snapshot never updates policy and is
  // discarded atomically with the committed bundle.
  logic        hold_valid_q;
  logic [15:0] hold_requests_q;
  logic [15:0] selection_requests;
  logic [2:0] row_pick [0:3];

  logic [3:0] selected_rows;
  logic [15:0] selected_sources;
  logic [DEBT_WIDTH-1:0] debt_work [0:3];
  logic [2:0] phase_work;
  logic token_work;
  logic [1:0] debt_rr_work;
  logic [1:0] debt_scan_start;
  logic [1:0] fallback_rr_work;
  logic [2:0] pick;
  logic consume_token;
  logic found_fallback;
  integer offer_count;
  integer offset;
  integer candidate_row;
  integer candidate_source;
  integer scheduled_row;
  integer token_attempt;

  function automatic logic [2:0] choose_column(
    input logic [3:0] requests,
    input logic [1:0] start
  );
    integer step;
    logic [1:0] index;
    logic found;
    logic [1:0] chosen;
    begin
      found = 1'b0;
      chosen = start;
      for (step = 0; step < 4; step = step + 1) begin
        index = start + 2'(step);
        if (!found && requests[index]) begin
          found = 1'b1;
          chosen = 2'(index);
        end
      end
      choose_column = {found, chosen};
    end
  endfunction

  function automatic integer token_row(
    input logic [2:0] phase_value,
    input logic token_value
  );
    begin
`ifdef A4_PCCK2_MUTATE_FLAT_WEIGHT
      token_row = token_value ? 2 : 1;
`else
      if (phase_value < 3'd5)
        token_row = token_value ? 2 : 1;
      else
        token_row = token_value ? 3 : 0;
`endif
    end
  endfunction

  assign selection_requests = hold_valid_q ? hold_requests_q : source_valid;

  always_comb begin
    phase_work = phase_q;
    token_work = token_q;
    debt_rr_work = debt_rr_q;
    debt_scan_start = debt_rr_q;
    fallback_rr_work = fallback_rr_q;
    selected_rows = '0;
    selected_sources = '0;
    grant_addr = '0;
    offer_count = 0;
    pick = '0;
    consume_token = 1'b0;
    found_fallback = 1'b0;
    candidate_row = 0;
    candidate_source = -1;
    scheduled_row = 0;
    for (int row_i = 0; row_i < 4; row_i = row_i + 1) begin
      column_n[row_i] = column_q[row_i];
      debt_work[row_i] = debt_q[row_i];
      debt_n[row_i] = debt_q[row_i];
      // Exactly four physical column arbiters.  Every later policy choice
      // consumes only this row-local valid/column summary.
      row_pick[row_i] = choose_column(
        selection_requests[row_i*4 +: 4], column_q[row_i]
      );
    end

    // Repay at most one grant per owed row, preserving a shallow two-row
    // offer and avoiding a global 16-way winner mux.
    for (offset = 0; offset < 4; offset = offset + 1) begin
      candidate_row = (integer'(debt_scan_start) + offset) & 3;
      pick = row_pick[candidate_row];
      if ((offer_count < 2) && (debt_work[candidate_row] != '0) &&
          !selected_rows[candidate_row] && pick[2]) begin
        candidate_source = candidate_row*4 + integer'(pick[1:0]);
        grant_addr[offer_count*4 +: 4] = 4'(candidate_source);
        offer_count = offer_count + 1;
        selected_rows[candidate_row] = 1'b1;
        selected_sources[candidate_source] = 1'b1;
        debt_work[candidate_row] = debt_work[candidate_row] - 1'b1;
        debt_rr_work = 2'((candidate_row + 1) & 3);
      end
    end

    // Spend at most two committed tokens in token order.  Borrowing records
    // debt.  Saturated debt deliberately freezes token advancement.
    for (token_attempt = 0; token_attempt < 2; token_attempt = token_attempt + 1) begin
      if (offer_count < 2) begin
        scheduled_row = token_row(phase_work, token_work);
        pick = row_pick[scheduled_row];
        consume_token = 1'b0;
        candidate_source = -1;
        if (!selected_rows[scheduled_row] && pick[2]) begin
          candidate_source = scheduled_row*4 + integer'(pick[1:0]);
          consume_token = 1'b1;
        end else begin
          found_fallback = 1'b0;
          for (offset = 0; offset < 4; offset = offset + 1) begin
            candidate_row = (integer'(fallback_rr_work) + offset) & 3;
            pick = row_pick[candidate_row];
            if (!found_fallback && !selected_rows[candidate_row] && pick[2]) begin
              found_fallback = 1'b1;
              candidate_source = candidate_row*4 + integer'(pick[1:0]);
              fallback_rr_work = 2'((candidate_row + 1) & 3);
            end
          end
          if (found_fallback) begin
`ifndef A4_PCCK2_MUTATE_DROP_DEBT
            if (debt_work[scheduled_row] != DEBT_MAX) begin
              debt_work[scheduled_row] = debt_work[scheduled_row] + 1'b1;
              consume_token = 1'b1;
            end
`else
            consume_token = 1'b1;
`endif
          end
        end

        if (candidate_source >= 0) begin
          candidate_row = candidate_source >> 2;
          grant_addr[offer_count*4 +: 4] = 4'(candidate_source);
          offer_count = offer_count + 1;
          selected_rows[candidate_row] = 1'b1;
          selected_sources[candidate_source] = 1'b1;
          if (consume_token) begin
            if (!token_work) begin
              token_work = 1'b1;
            end else begin
              token_work = 1'b0;
              phase_work = (phase_work == 3'd5) ? 3'd0 : phase_work + 3'd1;
            end
          end
        end
      end
    end

    grant_count = 2'(offer_count);
    for (int row_i = 0; row_i < 4; row_i = row_i + 1)
      if (selected_rows[row_i])
        column_n[row_i] = row_pick[row_i][1:0] + 2'd1;
    phase_n = phase_work;
    token_n = token_work;
    debt_rr_n = debt_rr_work;
    fallback_rr_n = fallback_rr_work;
    for (int row_i = 0; row_i < 4; row_i = row_i + 1)
      debt_n[row_i] = debt_work[row_i];
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      phase_q <= '0;
      token_q <= 1'b0;
      debt_rr_q <= '0;
      fallback_rr_q <= '0;
      hold_valid_q <= 1'b0;
      hold_requests_q <= '0;
      for (int row_i = 0; row_i < 4; row_i = row_i + 1) begin
        column_q[row_i] <= '0;
        debt_q[row_i] <= '0;
      end
    end else begin
`ifdef A4_PCCK2_MUTATE_STALL_ADVANCE
      if (grant_count != 0) begin
`else
      if (bundle_ready && (grant_count != 0)) begin
`endif
        phase_q <= phase_n;
        token_q <= token_n;
        debt_rr_q <= debt_rr_n;
        fallback_rr_q <= fallback_rr_n;
        for (int row_i = 0; row_i < 4; row_i = row_i + 1) begin
          column_q[row_i] <= column_n[row_i];
          debt_q[row_i] <= debt_n[row_i];
        end
      end

      if (bundle_ready) begin
        hold_valid_q <= 1'b0;
      end else if (!hold_valid_q && (grant_count != 0)) begin
        hold_valid_q <= 1'b1;
        hold_requests_q <= source_valid;
      end
    end
  end

  assign drain_idle = !(|source_valid) && !hold_valid_q;
  assign source_ready = bundle_ready ? selected_sources : 16'b0;

`ifndef SYNTHESIS
  logic [1:0] blocked_count_q;
  logic [7:0] blocked_addr_q;
  logic blocked_q;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      blocked_q <= 1'b0;
      blocked_count_q <= '0;
      blocked_addr_q <= '0;
    end else begin
      if (blocked_q && !bundle_ready) begin
        assert (grant_count == blocked_count_q);
        assert (grant_addr == blocked_addr_q);
      end
      blocked_q <= (grant_count != 0) && !bundle_ready;
      blocked_count_q <= grant_count;
      blocked_addr_q <= grant_addr;
      assert (grant_count <= 2);
      assert ($countones(source_ready) ==
              (bundle_ready ? integer'(grant_count) : 0));
      assert ((source_ready & ~selection_requests) == '0);
      assert (!((grant_count == 2) && (grant_addr[3:0] == grant_addr[7:4])));
    end
  end
`endif
endmodule
