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

  function automatic logic [3:0] choose_row_onehot(
      input logic [3:0] active_rows,
      input logic [1:0] preferred
  );
    begin
      choose_row_onehot = 4'b0000;
      case (preferred)
        2'd0: begin
          if (active_rows[0])      choose_row_onehot = 4'b0001;
          else if (active_rows[1]) choose_row_onehot = 4'b0010;
          else if (active_rows[2]) choose_row_onehot = 4'b0100;
          else if (active_rows[3]) choose_row_onehot = 4'b1000;
        end
        2'd1: begin
          if (active_rows[1])      choose_row_onehot = 4'b0010;
          else if (active_rows[2]) choose_row_onehot = 4'b0100;
          else if (active_rows[3]) choose_row_onehot = 4'b1000;
          else if (active_rows[0]) choose_row_onehot = 4'b0001;
        end
        2'd2: begin
          if (active_rows[2])      choose_row_onehot = 4'b0100;
          else if (active_rows[3]) choose_row_onehot = 4'b1000;
          else if (active_rows[0]) choose_row_onehot = 4'b0001;
          else if (active_rows[1]) choose_row_onehot = 4'b0010;
        end
        default: begin
          if (active_rows[3])      choose_row_onehot = 4'b1000;
          else if (active_rows[0]) choose_row_onehot = 4'b0001;
          else if (active_rows[1]) choose_row_onehot = 4'b0010;
          else if (active_rows[2]) choose_row_onehot = 4'b0100;
        end
      endcase
    end
  endfunction

  function automatic logic [3:0] pick_column_onehot(
      input logic [3:0] row_req,
      input logic [1:0] pointer
  );
    begin
      pick_column_onehot = 4'b0000;
      case (pointer)
        2'd0: begin
          if (row_req[0])      pick_column_onehot = 4'b0001;
          else if (row_req[1]) pick_column_onehot = 4'b0010;
          else if (row_req[2]) pick_column_onehot = 4'b0100;
          else if (row_req[3]) pick_column_onehot = 4'b1000;
        end
        2'd1: begin
          if (row_req[1])      pick_column_onehot = 4'b0010;
          else if (row_req[2]) pick_column_onehot = 4'b0100;
          else if (row_req[3]) pick_column_onehot = 4'b1000;
          else if (row_req[0]) pick_column_onehot = 4'b0001;
        end
        2'd2: begin
          if (row_req[2])      pick_column_onehot = 4'b0100;
          else if (row_req[3]) pick_column_onehot = 4'b1000;
          else if (row_req[0]) pick_column_onehot = 4'b0001;
          else if (row_req[1]) pick_column_onehot = 4'b0010;
        end
        default: begin
          if (row_req[3])      pick_column_onehot = 4'b1000;
          else if (row_req[0]) pick_column_onehot = 4'b0001;
          else if (row_req[1]) pick_column_onehot = 4'b0010;
          else if (row_req[2]) pick_column_onehot = 4'b0100;
        end
      endcase
    end
  endfunction

  function automatic logic [1:0] index_onehot4(input logic [3:0] onehot);
    begin
      if (onehot[0])      index_onehot4 = 2'd0;
      else if (onehot[1]) index_onehot4 = 2'd1;
      else if (onehot[2]) index_onehot4 = 2'd2;
      else                index_onehot4 = 2'd3;
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
  logic [3:0] selected_row_onehot;
  logic [3:0] selected_column_onehot;
  logic [3:0] selected_row_req;
  logic [1:0] selected_row_ptr;
  logic [3:0] selected_source;
  logic [15:0] selected_bitmap;
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
    selected_row_onehot = 4'd0;
    selected_column_onehot = 4'd0;
    selected_row_req = 4'd0;
    selected_row_ptr = 2'd0;
    selected_source = 4'd0;
    selected_bitmap = 16'd0;
    token_cursor_d = token_cursor_q;
    for (row_index = 0; row_index < 4; row_index = row_index + 1) begin
      scan_ptr[row_index] = row_ptr_q[row_index];
      row_ptr_d[row_index] = row_ptr_q[row_index];
    end

    for (lane = 0; lane < 2; lane = lane + 1) begin
      active_rows[0] = |work_req[3:0];
      active_rows[1] = |work_req[7:4];
      active_rows[2] = |work_req[11:8];
      active_rows[3] = |work_req[15:12];
      // The second calendar slot is fixed whenever lane 0 is valid.  If lane 0
      // is invalid then no request exists, so lane 1 is invalid regardless of
      // its preferred row.  Computing both slots from registered state avoids
      // placing token_inc/calendar decode in the lane-0-to-lane-1 data cone.
      if (lane == 0)
        preferred_row = calendar_row(token_cursor_q);
      else
        preferred_row = calendar_row(token_inc(token_cursor_q));
      selected_row_onehot = choose_row_onehot(active_rows, preferred_row);
      selected_row_req =
          ({4{selected_row_onehot[0]}} & work_req[3:0]) |
          ({4{selected_row_onehot[1]}} & work_req[7:4]) |
          ({4{selected_row_onehot[2]}} & work_req[11:8]) |
          ({4{selected_row_onehot[3]}} & work_req[15:12]);
      selected_row_ptr =
          ({2{selected_row_onehot[0]}} & scan_ptr[0]) |
          ({2{selected_row_onehot[1]}} & scan_ptr[1]) |
          ({2{selected_row_onehot[2]}} & scan_ptr[2]) |
          ({2{selected_row_onehot[3]}} & scan_ptr[3]);
      selected_column_onehot = pick_column_onehot(
          selected_row_req, selected_row_ptr);
      selected_source = {index_onehot4(selected_row_onehot),
                         index_onehot4(selected_column_onehot)};
      selected_bitmap[3:0] =
          {4{selected_row_onehot[0]}} & selected_column_onehot;
      selected_bitmap[7:4] =
          {4{selected_row_onehot[1]}} & selected_column_onehot;
      selected_bitmap[11:8] =
          {4{selected_row_onehot[2]}} & selected_column_onehot;
      selected_bitmap[15:12] =
          {4{selected_row_onehot[3]}} & selected_column_onehot;
      if ((|selected_row_onehot) && (|selected_column_onehot)) begin
        fresh_count = fresh_count + 2'd1;
        if (lane == 0)
          fresh_addr0 = selected_source;
        else
          fresh_addr1 = selected_source;
        fresh_bitmap = fresh_bitmap | selected_bitmap;
        work_req = work_req & ~selected_bitmap;
        for (row_index = 0; row_index < 4; row_index = row_index + 1) begin
          if (selected_row_onehot[row_index])
            scan_ptr[row_index] = index_onehot4(selected_column_onehot) + 2'd1;
        end
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
