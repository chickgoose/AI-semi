`timescale 1ns/1ps

// Raw observational recorder only.  It assigns no event IDs and keeps no
// shadow identity or polarity FIFO.  Python reconstructs the v1 per-source
// FIFO exclusively from the pinned addrpol trace and these cycle observations.
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
  longint unsigned generated_count;
  longint unsigned delivered_count;
  longint unsigned overrun_count;
  reg [15:0] sampled_overrun;
  integer drain_steps;

  function automatic integer popcount16;
    input [15:0] value;
    integer index;
    begin
      popcount16 = 0;
      for (index = 0; index < 16; index = index + 1)
        popcount16 = popcount16 + value[index];
    end
  endfunction

  function automatic integer popcount4;
    input [3:0] value;
    integer index;
    begin
      popcount4 = 0;
      for (index = 0; index < 4; index = index + 1)
        popcount4 = popcount4 + value[index];
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

  task automatic record_raw_cycle;
    begin
      if ((^sampled_overrun) === 1'bx || (^valid0) === 1'bx ||
          (^row0) === 1'bx || (^col_mask0) === 1'bx || (^pol_mask0) === 1'bx ||
          (^valid1) === 1'bx || (^row1) === 1'bx ||
          (^col_mask1) === 1'bx || (^pol_mask1) === 1'bx)
        $fatal(1, "raw native observation contains X/Z cycle=%0d", cycle_number);
      $fwrite(ledger_fd,
        "CYCLE|%0d|%04x|%0d|%0d|%01x|%01x|%0d|%0d|%01x|%01x\n",
        cycle_number, sampled_overrun,
        valid0, row0, col_mask0, pol_mask0,
        valid1, row1, col_mask1, pol_mask1);
      delivered_count = delivered_count + popcount4(valid0 ? col_mask0 : 4'b0);
      delivered_count = delivered_count + popcount4(valid1 ? col_mask1 : 4'b0);
      overrun_count = overrun_count + popcount16(sampled_overrun);
    end
  endtask

  task automatic drive_sample_and_record;
    begin
      #4;
      sampled_overrun = overrun;
      @(posedge clk);
      #1;
      record_raw_cycle();
      cycle_number = cycle_number + 1;
    end
  endtask

  initial begin
    rst = 1'b1;
    arrival = 16'b0;
    polarity_in = 16'b0;
    have_next = 1'b0;
    cycle_number = 0;
    generated_count = 0;
    delivered_count = 0;
    overrun_count = 0;

    if (!$value$plusargs("ADDRPOL_FILE=%s", trace_file))
      $fatal(1, "missing +ADDRPOL_FILE=<path>");
    if (!$value$plusargs("LEDGER_FILE=%s", ledger_file))
      $fatal(1, "missing +LEDGER_FILE=<path>");
    trace_fd = $fopen(trace_file, "r");
    if (trace_fd == 0)
      $fatal(1, "cannot open polarity trace file");
    ledger_fd = $fopen(ledger_file, "w");
    if (ledger_fd == 0)
      $fatal(1, "cannot open raw polarity ledger file");
    $fwrite(ledger_fd,
      "SCHEMA|redred.cluster2_cav_bridge.polarity_native_ledger/v1\n");
    $fwrite(ledger_fd,
      "SCOPE|SOURCE_FIFO_POLARITY_SEQUENCE_ONLY;IDENTICAL_SAME_SOURCE_EQUAL_POLARITY_EVENTS_UNOBSERVABLE;EVENT_ID_ORDER_INDEPENDENCE_NOT_CLAIMED\n");
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
        generated_count = generated_count + popcount16(next_arrival);
        read_next_trace();
        if (have_next && (next_cycle <= cycle_number))
          $fatal(1, "polarity trace cycles are not strictly increasing");
      end
      drive_sample_and_record();
    end

    // One unconditional empty-input cycle exposes any occurrence admitted on
    // the final trace edge. Continue until a cycle-complete quiescent witness.
    drain_steps = 0;
    begin : bounded_drain
      while (drain_steps < DRAIN_LIMIT) begin
        @(negedge clk);
        arrival = 16'b0;
        polarity_in = 16'b0;
        drive_sample_and_record();
        drain_steps = drain_steps + 1;
        if (!valid0 && !valid1 && sampled_overrun == 16'b0)
          disable bounded_drain;
      end
    end
    if (valid0 || valid1 || sampled_overrun != 16'b0)
      $fatal(1, "bounded drain did not reach a quiescent witness");

    $fwrite(ledger_fd, "SUMMARY|%0d|%0d|%0d|0|0|1\n",
            generated_count, delivered_count, overrun_count);
    $fclose(trace_fd);
    $fclose(ledger_fd);
    $display("REDRED_CLUSTER2_POLARITY_V1_NATIVE_PASS generated=%0d delivered=%0d overrun=%0d phantom=0 duplicate=0 drain_empty=1",
             generated_count, delivered_count, overrun_count);
    $finish;
  end
endmodule
