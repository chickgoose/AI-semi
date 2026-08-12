module a2_batched_iwrr_k2 (
    input  logic        clk,
    input  logic        rst,
    input  logic [15:0] req,
    output logic [1:0]  grant_count,
    output logic [3:0]  grant_addr0,
    output logic [3:0]  grant_addr1,
    output logic [15:0] grant_bitmap,
    input  logic        bundle_ready,
    output logic        drain_idle
);
  // The cursor addresses the next microstep in the cyclic row calendar
  //   1,2,0,1,2,3,1,2,1,2,1,2.
  // A committed bundle advances by exactly grant_count microsteps.
  logic [3:0] token_cursor_q, token_cursor_d;
  logic [1:0] row_ptr_q [0:3];
  logic [1:0] row_ptr_d [0:3];
  logic       hold_q, hold_d;
  logic       hold_two_q, hold_two_d;
  logic [3:0] hold_addr0_q, hold_addr0_d;
  logic [3:0] hold_addr1_q, hold_addr1_d;

  function automatic logic [1:0] calendar_row(input logic [3:0] token);
    case (token)
      4'd0:  calendar_row = 2'd1;
      4'd1:  calendar_row = 2'd2;
      4'd2:  calendar_row = 2'd0;
      4'd3:  calendar_row = 2'd1;
      4'd4:  calendar_row = 2'd2;
      4'd5:  calendar_row = 2'd3;
      4'd6:  calendar_row = 2'd1;
      4'd7:  calendar_row = 2'd2;
      4'd8:  calendar_row = 2'd1;
      4'd9:  calendar_row = 2'd2;
      4'd10: calendar_row = 2'd1;
      default: calendar_row = 2'd2;
    endcase
  endfunction

  function automatic logic [3:0] token_inc(input logic [3:0] token);
    token_inc = (token == 4'd11) ? 4'd0 : token + 4'd1;
  endfunction

  function automatic logic [2:0] choose_row(
      input logic [3:0] active_rows,
      input logic [1:0] preferred
  );
    begin
      if (active_rows[preferred])
        choose_row = {1'b1, preferred};
      else if (active_rows[preferred + 2'd1])
        choose_row = {1'b1, preferred + 2'd1};
      else if (active_rows[preferred + 2'd2])
        choose_row = {1'b1, preferred + 2'd2};
      else if (active_rows[preferred + 2'd3])
        choose_row = {1'b1, preferred + 2'd3};
      else
        choose_row = 3'b000;
    end
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

  logic [1:0] fresh_count;
  logic [3:0] fresh_addr0, fresh_addr1;
  logic [15:0] fresh_bitmap;
  logic [15:0] work_req;
  logic [3:0] active_rows;
  logic [3:0] scan_cursor;
  logic [1:0] scan_ptr [0:3];
  logic [1:0] preferred_row;
  logic [2:0] selected_row;
  logic [2:0] selected_column;
  logic [3:0] selected_source;
  integer lane;
  integer row_index;

  always_comb begin
    fresh_count = 2'd0;
    fresh_addr0 = 4'd0;
    fresh_addr1 = 4'd0;
    fresh_bitmap = 16'd0;
    work_req = req;
    active_rows = 4'd0;
    scan_cursor = token_cursor_q;
    preferred_row = 2'd0;
    selected_row = 3'd0;
    selected_column = 3'd0;
    selected_source = 4'd0;
    token_cursor_d = token_cursor_q;
    for (row_index = 0; row_index < 4; row_index = row_index + 1) begin
      scan_ptr[row_index] = row_ptr_q[row_index];
      row_ptr_d[row_index] = row_ptr_q[row_index];
    end

    for (lane = 0; lane < 2; lane = lane + 1) begin
      for (row_index = 0; row_index < 4; row_index = row_index + 1)
        active_rows[row_index] = |work_req[row_index*4 +: 4];
      preferred_row = calendar_row(scan_cursor);
      selected_row = choose_row(active_rows, preferred_row);
      selected_column = pick_column(
          work_req[selected_row[1:0]*4 +: 4], scan_ptr[selected_row[1:0]]);
      if (selected_row[2] && selected_column[2]) begin
        selected_source = {selected_row[1:0], selected_column[1:0]};
        fresh_count = fresh_count + 2'd1;
        if (lane == 0)
          fresh_addr0 = selected_source;
        else
          fresh_addr1 = selected_source;
        fresh_bitmap[selected_source] = 1'b1;
        work_req[selected_source] = 1'b0;
        scan_ptr[selected_row[1:0]] = selected_column[1:0] + 2'd1;
        scan_cursor = token_inc(scan_cursor);
      end
    end

    hold_d = hold_q;
    hold_two_d = hold_two_q;
    hold_addr0_d = hold_addr0_q;
    hold_addr1_d = hold_addr1_q;

    if (hold_q) begin
      grant_count = hold_two_q ? 2'd2 : 2'd1;
      grant_addr0 = hold_addr0_q;
      grant_addr1 = hold_addr1_q;
      grant_bitmap = 16'd0;
      grant_bitmap[hold_addr0_q] = 1'b1;
      if (hold_two_q)
        grant_bitmap[hold_addr1_q] = 1'b1;
      if (bundle_ready) begin
        token_cursor_d = token_inc(token_cursor_q);
        if (hold_two_q)
          token_cursor_d = token_inc(token_inc(token_cursor_q));
        row_ptr_d[hold_addr0_q[3:2]] = hold_addr0_q[1:0] + 2'd1;
        if (hold_two_q)
          row_ptr_d[hold_addr1_q[3:2]] = hold_addr1_q[1:0] + 2'd1;
        hold_d = 1'b0;
      end
    end else begin
      grant_count = fresh_count;
      grant_addr0 = fresh_addr0;
      grant_addr1 = fresh_addr1;
      grant_bitmap = fresh_bitmap;
      if (fresh_count != 0) begin
        if (bundle_ready) begin
          token_cursor_d = scan_cursor;
          for (row_index = 0; row_index < 4; row_index = row_index + 1)
            row_ptr_d[row_index] = scan_ptr[row_index];
        end else begin
          hold_d = 1'b1;
          hold_two_d = (fresh_count == 2);
          hold_addr0_d = fresh_addr0;
          hold_addr1_d = fresh_addr1;
        end
      end
    end
    drain_idle = (req == 16'd0) && !hold_q;
    if (rst) begin
      grant_count = 2'd0;
      grant_addr0 = 4'd0;
      grant_addr1 = 4'd0;
      grant_bitmap = 16'd0;
      drain_idle = (req == 16'd0);
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      token_cursor_q <= 4'd0;
      row_ptr_q[0] <= 2'd0;
      row_ptr_q[1] <= 2'd0;
      row_ptr_q[2] <= 2'd0;
      row_ptr_q[3] <= 2'd0;
      hold_q <= 1'b0;
      hold_two_q <= 1'b0;
      hold_addr0_q <= 4'd0;
      hold_addr1_q <= 4'd0;
    end else begin
      token_cursor_q <= token_cursor_d;
      row_ptr_q[0] <= row_ptr_d[0];
      row_ptr_q[1] <= row_ptr_d[1];
      row_ptr_q[2] <= row_ptr_d[2];
      row_ptr_q[3] <= row_ptr_d[3];
      hold_q <= hold_d;
      hold_two_q <= hold_two_d;
      hold_addr0_q <= hold_addr0_d;
      hold_addr1_q <= hold_addr1_d;
    end
  end
endmodule
