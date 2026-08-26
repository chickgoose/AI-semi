`timescale 1ns/1ps

// Observational only: TB identity/polarity FIFOs check the DUT but never drive
// arbitration, admission, retirement, or the DUT's stored polarity state.
module redred_cluster2_polarity_v1_native_observational_tb;
  parameter integer DRAIN_LIMIT = 100000;

  reg clk = 1'b0;
  reg rst;
  reg [15:0] arrival;
  reg [15:0] polarity_in;
  wire [15:0] overrun;
  wire valid0;
  wire [1:0] row0;
  wire [3:0] col_mask0;
  wire [3:0] pol_mask0;
  wire valid1;
  wire [1:0] row1;
  wire [3:0] col_mask1;
  wire [3:0] pol_mask1;

  aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity dut (
    .clk(clk), .rst(rst), .arrival(arrival), .polarity_in(polarity_in),
    .overrun(overrun),
    .valid0(valid0), .row0(row0), .col_mask0(col_mask0), .pol_mask0(pol_mask0),
    .valid1(valid1), .row1(row1), .col_mask1(col_mask1), .pol_mask1(pol_mask1)
  );

  always #5 clk = ~clk;

  reg [4095:0] trace_file;
  reg [4095:0] ledger_file;
  integer trace_fd;
  integer ledger_fd;
  integer scan_result;
  longint unsigned next_cycle;
  reg [15:0] next_arrival;
  reg [15:0] next_polarity;
  reg have_next;
  longint unsigned cycle_number;
  longint unsigned next_event_id;
  longint unsigned generated_count;
  longint unsigned delivered_count;
  longint unsigned overrun_count;
  longint unsigned polarity_checked_count;
  integer source_index;
  integer fifo_count [0:15];
  longint unsigned fifo_event_id [0:15][0:1];
  longint unsigned fifo_occurrence_cycle [0:15][0:1];
  reg fifo_polarity [0:15][0:1];
  longint unsigned current_event_id [0:15];
  longint unsigned current_occurrence_cycle [0:15];
  reg current_polarity [0:15];
  reg [15:0] sampled_overrun;
  reg [15:0] accepted_mask;
  integer drain_steps;

  function automatic integer total_fifo_count;
    integer index;
    begin
      total_fifo_count = 0;
      for (index = 0; index < 16; index = index + 1)
        total_fifo_count = total_fifo_count + fifo_count[index];
    end
  endfunction

  task automatic read_next_trace;
    begin
      scan_result = $fscanf(
        trace_fd, "%d %h %h\n", next_cycle, next_arrival, next_polarity
      );
      if (scan_result == 3) begin
        if (next_arrival == 16'b0)
          $fatal(1, "polarity trace contains a zero arrival bitmap");
        if ((next_polarity & ~next_arrival) != 16'b0)
          $fatal(1, "polarity trace sets polarity outside arrival bitmap");
        have_next = 1'b1;
      end else if ($feof(trace_fd)) begin
        have_next = 1'b0;
      end else begin
        $fatal(1, "polarity trace contains a malformed row");
      end
    end
  endtask

  task automatic check_native_lanes;
    begin
      if ((^valid0) === 1'bx || (^row0) === 1'bx ||
          (^col_mask0) === 1'bx || (^pol_mask0) === 1'bx)
        $fatal(1, "lane0 contains X/Z cycle=%0d", cycle_number);
      if ((^valid1) === 1'bx || (^row1) === 1'bx ||
          (^col_mask1) === 1'bx || (^pol_mask1) === 1'bx)
        $fatal(1, "lane1 contains X/Z cycle=%0d", cycle_number);
      if (valid0 && !((row0 == 2'd0) || (row0 == 2'd1) || (row0 == 2'd2)))
        $fatal(1, "lane0 selected forbidden row=%0d cycle=%0d", row0, cycle_number);
      if (valid1 && !((row1 == 2'd0) || (row1 == 2'd2) || (row1 == 2'd3)))
        $fatal(1, "lane1 selected forbidden row=%0d cycle=%0d", row1, cycle_number);
      if (valid0 && (col_mask0 == 4'b0))
        $fatal(1, "lane0 valid with empty bitmap cycle=%0d", cycle_number);
      if (valid1 && (col_mask1 == 4'b0))
        $fatal(1, "lane1 valid with empty bitmap cycle=%0d", cycle_number);
      if (!valid0 && ((col_mask0 != 4'b0) || (pol_mask0 != 4'b0)))
        $fatal(1, "lane0 invalid with nonempty output cycle=%0d", cycle_number);
      if (!valid1 && ((col_mask1 != 4'b0) || (pol_mask1 != 4'b0)))
        $fatal(1, "lane1 invalid with nonempty output cycle=%0d", cycle_number);
      if ((pol_mask0 & ~col_mask0) != 4'b0 || (pol_mask1 & ~col_mask1) != 4'b0)
        $fatal(1, "polarity asserted outside retired columns cycle=%0d", cycle_number);
      if (valid0 && valid1 && (row0 == row1))
        $fatal(1, "native lanes selected the same row=%0d cycle=%0d", row0, cycle_number);
      if (valid0 && !valid1 && !((row0 == 2'd1) || (row0 == 2'd2)))
        $fatal(1, "lane0-only selected impossible row=%0d cycle=%0d", row0, cycle_number);
      if (!valid0 && valid1 && !((row1 == 2'd0) || (row1 == 2'd3)))
        $fatal(1, "lane1-only selected impossible row=%0d cycle=%0d", row1, cycle_number);
      if (valid0 && valid1 && !(
          ((row0 == 2'd0) && (row1 == 2'd3)) ||
          ((row0 == 2'd1) && (row1 == 2'd0)) ||
          ((row0 == 2'd1) && (row1 == 2'd2)) ||
          ((row0 == 2'd1) && (row1 == 2'd3)) ||
          ((row0 == 2'd2) && (row1 == 2'd0)) ||
          ((row0 == 2'd2) && (row1 == 2'd3))))
        $fatal(1, "native lanes selected impossible row pair=%0d,%0d cycle=%0d",
               row0, row1, cycle_number);
    end
  endtask

  task automatic retire_lane;
    input integer native_lane;
    input integer valid_in;
    input [1:0] row_in;
    input [3:0] col_mask_in;
    input [3:0] pol_mask_in;
    integer column;
    integer retired_source;
    longint unsigned retired_event_id;
    longint unsigned retired_occurrence_cycle;
    reg retired_polarity;
    begin
      if (valid_in) begin
        for (column = 0; column < 4; column = column + 1) begin
          if (col_mask_in[column]) begin
            retired_source = row_in * 4 + column;
            if (fifo_count[retired_source] == 0)
              $fatal(1, "phantom retirement source=%0d cycle=%0d lane=%0d",
                     retired_source, cycle_number, native_lane);
            retired_event_id = fifo_event_id[retired_source][0];
            retired_occurrence_cycle = fifo_occurrence_cycle[retired_source][0];
            retired_polarity = fifo_polarity[retired_source][0];
            if (pol_mask_in[column] !== retired_polarity)
              $fatal(1, "polarity mismatch source=%0d event=%0d cycle=%0d got=%0d expected=%0d",
                     retired_source, retired_event_id, cycle_number,
                     pol_mask_in[column], retired_polarity);
            if (fifo_count[retired_source] == 2) begin
              fifo_event_id[retired_source][0] = fifo_event_id[retired_source][1];
              fifo_occurrence_cycle[retired_source][0] =
                fifo_occurrence_cycle[retired_source][1];
              fifo_polarity[retired_source][0] = fifo_polarity[retired_source][1];
            end
            fifo_count[retired_source] = fifo_count[retired_source] - 1;
            delivered_count = delivered_count + 1;
            polarity_checked_count = polarity_checked_count + 1;
            $fwrite(ledger_fd,
              "EVENT|%0d|%0d|%0d|DELIVERED|%0d|%0d|%0d|%0d|%0d\n",
              retired_event_id, retired_source, retired_occurrence_cycle,
              cycle_number, native_lane, row_in, column, retired_polarity);
          end
        end
      end
    end
  endtask

  task automatic sample_retirement;
    begin
      check_native_lanes();
      retire_lane(0, valid0, row0, col_mask0, pol_mask0);
      retire_lane(1, valid1, row1, col_mask1, pol_mask1);
    end
  endtask

  initial begin
    rst = 1'b1;
    arrival = 16'b0;
    polarity_in = 16'b0;
    have_next = 1'b0;
    cycle_number = 0;
    next_event_id = 0;
    generated_count = 0;
    delivered_count = 0;
    overrun_count = 0;
    polarity_checked_count = 0;
    for (source_index = 0; source_index < 16; source_index = source_index + 1) begin
      fifo_count[source_index] = 0;
      fifo_event_id[source_index][0] = 0;
      fifo_event_id[source_index][1] = 0;
      fifo_occurrence_cycle[source_index][0] = 0;
      fifo_occurrence_cycle[source_index][1] = 0;
      fifo_polarity[source_index][0] = 1'b0;
      fifo_polarity[source_index][1] = 1'b0;
      current_event_id[source_index] = 0;
      current_occurrence_cycle[source_index] = 0;
      current_polarity[source_index] = 1'b0;
    end

    if (!$value$plusargs("ADDRPOL_FILE=%s", trace_file))
      $fatal(1, "missing +ADDRPOL_FILE=<path>");
    if (!$value$plusargs("LEDGER_FILE=%s", ledger_file))
      $fatal(1, "missing +LEDGER_FILE=<path>");
    trace_fd = $fopen(trace_file, "r");
    if (trace_fd == 0)
      $fatal(1, "cannot open polarity trace file");
    ledger_fd = $fopen(ledger_file, "w");
    if (ledger_fd == 0)
      $fatal(1, "cannot open ledger file");
    $fwrite(ledger_fd,
      "SCHEMA|redred.cluster2_cav_bridge.polarity_v1_native_ledger/v1\n");
    read_next_trace();

    repeat (2) @(posedge clk);
    @(negedge clk);
    rst = 1'b0;

    while (have_next) begin
      @(negedge clk);
      arrival = 16'b0;
      polarity_in = 16'b0;
      if (next_cycle < cycle_number)
        $fatal(1, "polarity trace cycles are not strictly increasing");
      if (next_cycle == cycle_number) begin
        arrival = next_arrival;
        polarity_in = next_polarity;
        for (source_index = 0; source_index < 16; source_index = source_index + 1) begin
          if (next_arrival[source_index]) begin
            current_event_id[source_index] = next_event_id;
            current_occurrence_cycle[source_index] = cycle_number;
            current_polarity[source_index] = next_polarity[source_index];
            next_event_id = next_event_id + 1;
            generated_count = generated_count + 1;
          end
        end
        read_next_trace();
        if (have_next && (next_cycle <= cycle_number))
          $fatal(1, "polarity trace cycles are not strictly increasing");
      end

      #4;
      sampled_overrun = overrun;
      if ((^sampled_overrun) === 1'bx)
        $fatal(1, "sampled overrun contains X/Z cycle=%0d", cycle_number);
      if ((sampled_overrun & ~arrival) != 16'b0)
        $fatal(1, "DUT overrun escaped the presented arrival bitmap");
      accepted_mask = arrival & ~sampled_overrun;
      for (source_index = 0; source_index < 16; source_index = source_index + 1) begin
        if (sampled_overrun[source_index] !==
            (arrival[source_index] && (fifo_count[source_index] == 2)))
          $fatal(1, "v1 pre-edge overrun differs from arrival-and-full source=%0d cycle=%0d",
                 source_index, cycle_number);
        if (sampled_overrun[source_index]) begin
          overrun_count = overrun_count + 1;
          $fwrite(ledger_fd, "EVENT|%0d|%0d|%0d|OVERRUN|-|-|-|-|%0d\n",
                  current_event_id[source_index], source_index,
                  current_occurrence_cycle[source_index],
                  current_polarity[source_index]);
        end
      end

      @(posedge clk);
      #1;
      sample_retirement();

      // Current-edge admissions enter the shadow only after old events retire.
      for (source_index = 0; source_index < 16; source_index = source_index + 1) begin
        if (accepted_mask[source_index]) begin
          if (fifo_count[source_index] >= 2)
            $fatal(1, "accepted occurrence exceeds TB FIFO depth");
          fifo_event_id[source_index][fifo_count[source_index]] =
            current_event_id[source_index];
          fifo_occurrence_cycle[source_index][fifo_count[source_index]] =
            current_occurrence_cycle[source_index];
          fifo_polarity[source_index][fifo_count[source_index]] =
            current_polarity[source_index];
          fifo_count[source_index] = fifo_count[source_index] + 1;
        end
      end
      cycle_number = cycle_number + 1;
    end

    arrival = 16'b0;
    polarity_in = 16'b0;
    drain_steps = 0;
    while ((total_fifo_count() != 0) || valid0 || valid1) begin
      if (drain_steps >= DRAIN_LIMIT)
        $fatal(1, "empty drain timed out pending=%0d", total_fifo_count());
      @(negedge clk);
      arrival = 16'b0;
      polarity_in = 16'b0;
      #4;
      sampled_overrun = overrun;
      if ((^sampled_overrun) === 1'bx || sampled_overrun != 16'b0)
        $fatal(1, "overrun invalid during empty-input drain cycle=%0d", cycle_number);
      @(posedge clk);
      #1;
      sample_retirement();
      cycle_number = cycle_number + 1;
      drain_steps = drain_steps + 1;
    end

    if (total_fifo_count() != 0)
      $fatal(1, "TB-only FIFO is not empty after drain");
    if (generated_count != delivered_count + overrun_count)
      $fatal(1, "conservation failed generated=%0d delivered=%0d overrun=%0d",
             generated_count, delivered_count, overrun_count);
    if (polarity_checked_count != delivered_count)
      $fatal(1, "not every delivery had polarity checked");
    if (next_event_id != generated_count)
      $fatal(1, "event ID accounting differs");

    $fwrite(ledger_fd, "SUMMARY|%0d|%0d|%0d|%0d\n",
            generated_count, delivered_count, overrun_count,
            polarity_checked_count);
    $fclose(trace_fd);
    $fclose(ledger_fd);
    $display("REDRED_CLUSTER2_POLARITY_V1_NATIVE_PASS generated=%0d delivered=%0d overrun=%0d polarity_checked=%0d",
             generated_count, delivered_count, overrun_count,
             polarity_checked_count);
    $finish;
  end
endmodule
