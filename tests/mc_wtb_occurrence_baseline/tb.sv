`timescale 1ps/1ps

module mc_wtb_occurrence_baseline_tb;
  localparam integer PAYLOAD_W = 102;
  localparam integer LANES = 6;
  localparam integer MAX_GROUPS = 1100;
  localparam integer HALF_PERIOD_PS = 3250;

  logic clk_i;
  logic rst_i;
  logic link_enable_i;
  logic [LANES-1:0] ingress_valid_i;
  logic [LANES*4-1:0] ingress_source_i;
  logic [LANES*PAYLOAD_W-1:0] ingress_payload_i;
  logic ingress_ready_o;
  logic ingress_commit_o;
  logic [1:0] accept_count_o;
  logic [3:0] accept_source0_o;
  logic [3:0] accept_source1_o;
  logic [PAYLOAD_W-1:0] accept_payload0_o;
  logic [PAYLOAD_W-1:0] accept_payload1_o;
  logic [1:0] retire_count_o;
  logic [3:0] retire_source0_o;
  logic [3:0] retire_source1_o;
  logic [PAYLOAD_W-1:0] retire_payload0_o;
  logic [PAYLOAD_W-1:0] retire_payload1_o;
  logic overflow_o;
  logic protocol_error_o;
  logic drain_idle_o;

  integer group_cycle [0:MAX_GROUPS-1];
  logic [LANES-1:0] group_mask [0:MAX_GROUPS-1];
  logic [LANES*4-1:0] group_sources [0:MAX_GROUPS-1];
  logic [LANES*PAYLOAD_W-1:0] group_payloads [0:MAX_GROUPS-1];
  integer group_count;
  integer group_cursor;
  integer input_fd;
  integer log_fd;
  integer status_fd;
  integer scan_count;
  integer cycle;
  integer lane;
  integer accepted_total;
  integer retired_total;
  integer ingress_total;
  integer drain_wait;
  string stimulus_path;
  string raw_log_path;
  string status_path;
  logic [3:0] scan_source [0:LANES-1];
  logic [PAYLOAD_W-1:0] scan_payload [0:LANES-1];

  mc_wtb_occurrence_baseline_top #(.PAYLOAD_W(PAYLOAD_W)) dut (.*);

  initial begin
    clk_i = 1'b0;
    forever #HALF_PERIOD_PS clk_i = ~clk_i;
  end

  task automatic clear_ingress;
    begin
      ingress_valid_i = {LANES{1'b0}};
      ingress_source_i = {(LANES*4){1'b0}};
      ingress_payload_i = {(LANES*PAYLOAD_W){1'b0}};
    end
  endtask

  task automatic record_outputs(input integer sampled_cycle);
    begin
      if (accept_count_o != 0) begin
        $fdisplay(log_fd, "ACCEPT,%0d,0,%0d,%026h", sampled_cycle,
                  accept_source0_o, accept_payload0_o);
        accepted_total = accepted_total + 1;
      end
      if (accept_count_o == 2) begin
        $fdisplay(log_fd, "ACCEPT,%0d,1,%0d,%026h", sampled_cycle,
                  accept_source1_o, accept_payload1_o);
        accepted_total = accepted_total + 1;
      end
      if (retire_count_o != 0) begin
        $fdisplay(log_fd, "RETIRE,%0d,0,%0d,%026h", sampled_cycle,
                  retire_source0_o, retire_payload0_o);
        retired_total = retired_total + 1;
      end
      if (retire_count_o == 2) begin
        $fdisplay(log_fd, "RETIRE,%0d,1,%0d,%026h", sampled_cycle,
                  retire_source1_o, retire_payload1_o);
        retired_total = retired_total + 1;
      end
    end
  endtask

  initial begin
    if (!$value$plusargs("STIMULUS=%s", stimulus_path))
      $fatal(1, "STIMULUS plusarg is required");
    if (!$value$plusargs("RAW_LOG=%s", raw_log_path))
      $fatal(1, "RAW_LOG plusarg is required");
    if (!$value$plusargs("STATUS=%s", status_path))
      $fatal(1, "STATUS plusarg is required");

    input_fd = $fopen(stimulus_path, "r");
    log_fd = $fopen(raw_log_path, "w");
    status_fd = $fopen(status_path, "w");
    if (input_fd == 0 || log_fd == 0 || status_fd == 0)
      $fatal(1, "failed to open phase-4 input/output files");

    group_count = 0;
    while (!$feof(input_fd)) begin
      scan_count = $fscanf(input_fd,
          "%d %h %h %h %h %h %h %h %h %h %h %h %h %h\n",
          group_cycle[group_count], group_mask[group_count],
          scan_source[0], scan_payload[0], scan_source[1], scan_payload[1],
          scan_source[2], scan_payload[2], scan_source[3], scan_payload[3],
          scan_source[4], scan_payload[4], scan_source[5], scan_payload[5]);
      if (scan_count == 14) begin
        for (lane = 0; lane < LANES; lane = lane + 1) begin
          group_sources[group_count][lane*4 +: 4] = scan_source[lane];
          group_payloads[group_count][lane*PAYLOAD_W +: PAYLOAD_W] =
              scan_payload[lane];
        end
        group_count = group_count + 1;
        if (group_count >= MAX_GROUPS)
          $fatal(1, "too many stimulus groups");
      end else if (!$feof(input_fd)) begin
        $fatal(1, "malformed stimulus line after group %0d", group_count);
      end
    end
    $fclose(input_fd);
    if (group_count == 0)
      $fatal(1, "empty stimulus");
    if (group_count != 642 || group_cycle[0] != 0 ||
        group_cycle[group_count-1] != 153693)
      $fatal(1, "qualified stimulus group/cycle contract differs");
    for (lane = 0; lane < group_count; lane = lane + 1) begin
      if (group_mask[lane] == 0 ||
          (group_mask[lane] & (group_mask[lane] + 1'b1)) != 0)
        $fatal(1, "stimulus mask is not a nonempty prefix at group %0d", lane);
      if (lane != 0 && group_cycle[lane] <= group_cycle[lane-1])
        $fatal(1, "stimulus group cycles are not strictly increasing");
    end

    rst_i = 1'b1;
    link_enable_i = 1'b1;
    clear_ingress();
    accepted_total = 0;
    retired_total = 0;
    ingress_total = 0;
    group_cursor = 0;
    repeat (4) @(posedge clk_i);
    @(negedge clk_i);
    rst_i = 1'b0;

    for (cycle = 0; cycle <= group_cycle[group_count-1]; cycle = cycle + 1) begin
      clear_ingress();
      if (group_cursor < group_count && group_cycle[group_cursor] == cycle) begin
        ingress_valid_i = group_mask[group_cursor];
        ingress_source_i = group_sources[group_cursor];
        ingress_payload_i = group_payloads[group_cursor];
        group_cursor = group_cursor + 1;
      end
      #1;
      if ((|ingress_valid_i) && !ingress_ready_o)
        $fatal(1, "occurrence batch rejected before edge at cycle %0d", cycle);
      @(posedge clk_i);
      #1;
      if ($isunknown({ingress_ready_o, ingress_commit_o, accept_count_o,
                      retire_count_o, overflow_o, protocol_error_o,
                      drain_idle_o}))
        $fatal(1, "unknown control output at cycle %0d", cycle);
      if (|ingress_valid_i) begin
        for (lane = 0; lane < LANES; lane = lane + 1) begin
          if (ingress_valid_i[lane]) begin
            $fdisplay(log_fd, "INGRESS,%0d,%0d,%0d,%026h", cycle, lane,
                      ingress_source_i[lane*4 +: 4],
                      ingress_payload_i[lane*PAYLOAD_W +: PAYLOAD_W]);
            ingress_total = ingress_total + 1;
          end
        end
      end
      record_outputs(cycle);
      if (overflow_o || protocol_error_o)
        $fatal(1, "sticky DUT error at cycle %0d", cycle);
      @(negedge clk_i);
    end
    clear_ingress();

    drain_wait = 0;
    while (!drain_idle_o && drain_wait < 128) begin
      @(posedge clk_i);
      #1;
      record_outputs(cycle);
      if (overflow_o || protocol_error_o)
        $fatal(1, "sticky DUT error during drain");
      @(negedge clk_i);
      cycle = cycle + 1;
      drain_wait = drain_wait + 1;
    end
    if (!drain_idle_o)
      $fatal(1, "drain timeout");
    if (group_cursor != group_count || ingress_total != 1100 ||
        accepted_total != 1100 || retired_total != 1100)
      $fatal(1, "count mismatch groups=%0d/%0d ingress=%0d accept=%0d retire=%0d",
             group_cursor, group_count, ingress_total, accepted_total,
             retired_total);

    $fdisplay(status_fd,
        "PASS ingress=%0d accepted=%0d retired=%0d last_cycle=%0d overflow=%0d protocol_error=%0d",
        ingress_total, accepted_total, retired_total, cycle - 1, overflow_o,
        protocol_error_o);
    $fclose(log_fd);
    $fclose(status_fd);
    $display("MC_WTB_OCCURRENCE_BASELINE_RTL_PASS");
    $finish;
  end
endmodule
