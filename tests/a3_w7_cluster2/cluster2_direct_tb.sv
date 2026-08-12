`timescale 1ns/1ps

module a3_w7_cluster2_direct_tb;
  localparam integer MAX_EVENTS = 200000;
  reg clk = 1'b0;
  reg rst = 1'b1;
  reg [15:0] pending = 16'h0000;
  reg [31:0] pending_id [0:15];
  integer pending_cycle [0:15];
  reg reset_direct = 1'b0;
  reg [15:0] reset_req = 16'h0000;

  wire valid0, valid1;
  wire [1:0] row0, row1;
  wire [3:0] col_mask0, col_mask1;
  reg [15:0] result_mask;
  wire [15:0] req = reset_direct ? reset_req : (pending & ~result_mask);

  integer event_cycle [0:MAX_EVENTS-1];
  integer event_id [0:MAX_EVENTS-1];
  integer event_source [0:MAX_EVENTS-1];
  integer stim_cycles, event_count, event_cursor;
  integer generated, accepted, delivered, overrun;
  integer center_delivered, peripheral_delivered;
  integer both_lane_cycles, max_events_cycle;
  integer latency_sum, max_latency;
  integer cycle_index, drain_cycles;
  integer fd, scan_count, source, col, lane_events, latency;
  string stim_path;
  string mode;

  always #5 clk = ~clk;

  aer_tx16_trad_rowcol_fovea_cluster2 dut (
    .clk(clk), .rst(rst), .req(req),
    .valid0(valid0), .row0(row0), .col_mask0(col_mask0),
    .valid1(valid1), .row1(row1), .col_mask1(col_mask1)
  );

  always @(*) begin
    result_mask = 16'h0000;
    if (valid0)
      for (col = 0; col < 4; col = col + 1)
        if (col_mask0[col]) result_mask[(row0 * 4) + col] = 1'b1;
    if (valid1)
      for (col = 0; col < 4; col = col + 1)
        if (col_mask1[col]) result_mask[(row1 * 4) + col] = 1'b1;
  end

  task automatic observe_result;
    integer s;
    begin
      if (valid0 && !((row0 == 2'd1) || (row0 == 2'd2)))
        $fatal(1, "W7_CLUSTER2 lane0 row violation row=%0d", row0);
      if (valid1 && !((row1 == 2'd0) || (row1 == 2'd3)))
        $fatal(1, "W7_CLUSTER2 lane1 row violation row=%0d", row1);
      if (valid0 && (col_mask0 == 4'b0000))
        $fatal(1, "W7_CLUSTER2 lane0 empty valid");
      if (valid1 && (col_mask1 == 4'b0000))
        $fatal(1, "W7_CLUSTER2 lane1 empty valid");
      if ((result_mask & ~pending) != 16'h0000)
        $fatal(1, "W7_CLUSTER2 duplicate/phantom mask=%h pending=%h", result_mask, pending);
      lane_events = 0;
      for (s = 0; s < 16; s = s + 1) begin
        if (result_mask[s]) begin
          // Common event metrics number occurrence and observed retirement
          // boundaries inclusively; a one-register path is latency two.
          latency = cycle_index - pending_cycle[s] + 1;
          if (latency < 0) $fatal(1, "W7_CLUSTER2 negative latency source=%0d", s);
          latency_sum = latency_sum + latency;
          if (latency > max_latency) max_latency = latency;
          delivered = delivered + 1;
          if ((s / 4 == 1) || (s / 4 == 2)) center_delivered = center_delivered + 1;
          else peripheral_delivered = peripheral_delivered + 1;
          pending[s] = 1'b0;
          lane_events = lane_events + 1;
        end
      end
      if (valid0 && valid1) both_lane_cycles = both_lane_cycles + 1;
      if (lane_events > max_events_cycle) max_events_cycle = lane_events;
    end
  endtask

  task automatic inject_cycle;
    integer s;
    begin
      while ((event_cursor < event_count) &&
             (event_cycle[event_cursor] == cycle_index)) begin
        s = event_source[event_cursor];
        if ((s < 0) || (s >= 16)) $fatal(1, "W7_CLUSTER2 source out of range %0d", s);
        generated = generated + 1;
        if (pending[s]) begin
          overrun = overrun + 1;
        end else begin
          pending[s] = 1'b1;
          pending_id[s] = event_id[event_cursor];
          pending_cycle[s] = cycle_index;
          accepted = accepted + 1;
        end
        event_cursor = event_cursor + 1;
      end
    end
  endtask

  task automatic run_reset_gate;
    begin
      reset_direct = 1'b1;
      reset_req = 16'hffff;
      rst = 1'b1;
      repeat (2) @(posedge clk);
      #1;
      if (valid0 !== 1'b0 || valid1 !== 1'b0)
        $fatal(1, "W7_CLUSTER2 reset did not clear both valids");
      @(negedge clk); rst = 1'b0;
      @(posedge clk); #1;
      if (!(valid0 && row0 == 2'd1 && col_mask0 == 4'hf &&
            valid1 && row1 == 2'd0 && col_mask1 == 4'hf))
        $fatal(1, "W7_CLUSTER2 first dual-lane result mismatch");
      @(negedge clk); rst = 1'b1;
      @(posedge clk); #1;
      if (valid0 !== 1'b0 || valid1 !== 1'b0)
        $fatal(1, "W7_CLUSTER2 mid-traffic reset leaked stale valid");
      reset_req = 16'h0000;
      @(negedge clk); rst = 1'b0;
      repeat (2) begin @(posedge clk); #1;
        if (valid0 !== 1'b0 || valid1 !== 1'b0)
          $fatal(1, "W7_CLUSTER2 post-reset quiet violation");
      end
      $display("W7_CLUSTER2_RESET_PASS");
    end
  endtask

  task automatic run_trace;
    begin
      if (!$value$plusargs("STIM=%s", stim_path)) $fatal(1, "missing +STIM");
      fd = $fopen(stim_path, "r");
      if (fd == 0) $fatal(1, "cannot open stimulus %s", stim_path);
      scan_count = $fscanf(fd, "%d %d\n", stim_cycles, event_count);
      if (scan_count != 2 || event_count < 0 || event_count > MAX_EVENTS)
        $fatal(1, "bad stimulus header");
      for (event_cursor = 0; event_cursor < event_count; event_cursor = event_cursor + 1) begin
        scan_count = $fscanf(fd, "%d %d %d\n",
                            event_cycle[event_cursor], event_id[event_cursor],
                            event_source[event_cursor]);
        if (scan_count != 3) $fatal(1, "bad stimulus event %0d", event_cursor);
      end
      $fclose(fd);

      generated = 0; accepted = 0; delivered = 0; overrun = 0;
      center_delivered = 0; peripheral_delivered = 0;
      both_lane_cycles = 0; max_events_cycle = 0;
      latency_sum = 0; max_latency = 0; event_cursor = 0;
      pending = 16'h0000; reset_direct = 1'b0; rst = 1'b1;
      repeat (2) @(posedge clk);
      @(negedge clk); rst = 1'b0;

      for (cycle_index = 0; cycle_index < stim_cycles; cycle_index = cycle_index + 1) begin
        @(negedge clk);
        observe_result();
        inject_cycle();
      end
      drain_cycles = 0;
      while (((pending != 16'h0000) || valid0 || valid1) && drain_cycles < 256) begin
        @(negedge clk);
        observe_result();
        drain_cycles = drain_cycles + 1;
        cycle_index = cycle_index + 1;
      end
      if (event_cursor != event_count) $fatal(1, "unconsumed stimulus events");
      if (pending != 16'h0000 || valid0 || valid1) $fatal(1, "drain timeout pending=%h", pending);
      if (generated != accepted + overrun) $fatal(1, "arrival accounting mismatch");
      if (accepted != delivered) $fatal(1, "accepted/delivered mismatch");
      $display("W7_CLUSTER2_RTL_PASS generated=%0d accepted=%0d delivered=%0d overrun=%0d center=%0d peripheral=%0d both_lane_cycles=%0d max_events_cycle=%0d latency_sum=%0d max_latency=%0d drain_cycles=%0d",
               generated, accepted, delivered, overrun, center_delivered,
               peripheral_delivered, both_lane_cycles, max_events_cycle,
               latency_sum, max_latency, drain_cycles);
    end
  endtask

  initial begin
    if (!$value$plusargs("MODE=%s", mode)) mode = "trace";
    if (mode == "reset") run_reset_gate();
    else if (mode == "trace") run_trace();
    else $fatal(1, "unknown MODE=%s", mode);
    $finish;
  end
endmodule
