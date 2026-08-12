module a2_batched_iwrr_k2 (
    input  logic        clk,
    input  logic        rst,
    input  logic [15:0] req,
    output logic [1:0]  grant_valid,
    output logic [3:0]  grant_addr0,
    output logic [3:0]  grant_addr1,
    output logic [15:0] grant_bitmap,
    input  logic        grant_ready,
    output logic        drain_idle
);
  // Six K2 batches consume the 12-token cyclic row calendar
  //   (1,2), (0,1), (2,3), (1,2), (1,2), (1,2).
  logic [2:0] phase_q, phase_d;
  logic [1:0] row_ptr_q [0:3];
  logic [1:0] row_ptr_d [0:3];

  function automatic logic [3:0] phase_rows(input logic [2:0] phase);
    case (phase)
      3'd0: phase_rows = {2'd2, 2'd1};
      3'd1: phase_rows = {2'd1, 2'd0};
      3'd2: phase_rows = {2'd3, 2'd2};
      default: phase_rows = {2'd2, 2'd1};
    endcase
  endfunction

  function automatic logic [2:0] pick_column(
      input logic [3:0] row_req,
      input logic [1:0] pointer
  );
    begin
      if (row_req[pointer])
        pick_column = {1'b1, pointer};
      else if (row_req[pointer + 2'd1])
        pick_column = {1'b1, pointer + 2'd1};
      else if (row_req[pointer + 2'd2])
        pick_column = {1'b1, pointer + 2'd2};
      else if (row_req[pointer + 2'd3])
        pick_column = {1'b1, pointer + 2'd3};
      else
        pick_column = 3'b000;
    end
  endfunction

  logic [3:0] scheduled_rows;
  logic [1:0] first_row, second_row;
  logic [2:0] first_pick, second_pick;
  logic any_valid;
  integer row_index;

  always_comb begin
    scheduled_rows = phase_rows(phase_q);
    first_row = scheduled_rows[1:0];
    second_row = scheduled_rows[3:2];
    first_pick = pick_column(req[first_row*4 +: 4], row_ptr_q[first_row]);
    second_pick = pick_column(req[second_row*4 +: 4], row_ptr_q[second_row]);

    grant_valid = 2'b00;
    grant_addr0 = 4'd0;
    grant_addr1 = 4'd0;
    grant_bitmap = 16'd0;
    if (first_pick[2]) begin
      grant_valid[0] = 1'b1;
      grant_addr0 = {first_row, first_pick[1:0]};
      grant_bitmap[{first_row, first_pick[1:0]}] = 1'b1;
    end
    if (second_pick[2]) begin
      if (first_pick[2]) begin
        grant_valid[1] = 1'b1;
        grant_addr1 = {second_row, second_pick[1:0]};
      end else begin
        grant_valid[0] = 1'b1;
        grant_addr0 = {second_row, second_pick[1:0]};
      end
      grant_bitmap[{second_row, second_pick[1:0]}] = 1'b1;
    end

    any_valid = first_pick[2] || second_pick[2];
    phase_d = phase_q;
    for (row_index = 0; row_index < 4; row_index = row_index + 1)
      row_ptr_d[row_index] = row_ptr_q[row_index];
    // A nonempty batch commits atomically.  An all-empty batch is waived and
    // advances without waiting, so sparse traffic cannot deadlock behind it.
    if ((!any_valid) || grant_ready) begin
      phase_d = (phase_q == 3'd5) ? 3'd0 : phase_q + 3'd1;
      if (any_valid && grant_ready) begin
        if (first_pick[2])
          row_ptr_d[first_row] = first_pick[1:0] + 2'd1;
        if (second_pick[2])
          row_ptr_d[second_row] = second_pick[1:0] + 2'd1;
      end
    end

    drain_idle = (req == 16'd0);
    if (rst) begin
      grant_valid = 2'b00;
      grant_addr0 = 4'd0;
      grant_addr1 = 4'd0;
      grant_bitmap = 16'd0;
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      phase_q <= 3'd0;
      row_ptr_q[0] <= 2'd0;
      row_ptr_q[1] <= 2'd0;
      row_ptr_q[2] <= 2'd0;
      row_ptr_q[3] <= 2'd0;
    end else begin
      phase_q <= phase_d;
      row_ptr_q[0] <= row_ptr_d[0];
      row_ptr_q[1] <= row_ptr_d[1];
      row_ptr_q[2] <= row_ptr_d[2];
      row_ptr_q[3] <= row_ptr_d[3];
    end
  end
endmodule
