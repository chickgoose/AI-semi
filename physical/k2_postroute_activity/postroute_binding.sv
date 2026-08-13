`timescale 1ps/1ps

`ifndef K2_POSTROUTE_DUT
  `error "K2_POSTROUTE_DUT must name the exact post-route top"
`endif
`ifndef K2_POSTROUTE_PERIOD_PS
  `error "K2_POSTROUTE_PERIOD_PS must name the exact physical period"
`endif
`ifndef K2_POSTROUTE_REF_HALF_PS
  `error "K2_POSTROUTE_REF_HALF_PS must name half the physical period"
`endif
`ifndef K2_POSTROUTE_SAMPLE_FIRST_RISE_PS
  `error "K2_POSTROUTE_SAMPLE_FIRST_RISE_PS must name the sample-clock phase"
`endif

// Testbench-only normalization around one exact Innovus post-route netlist.
// The DUT instance name is deliberately identical for all candidates because
// that hierarchy is the activity scope consumed by Innovus.
module aer_legacy_candidate_adapter #(
  parameter int NUM_SOURCES  = 16,
  parameter int ADDR_WIDTH   = 16,
  parameter int RETIRE_LANES = 2,
  parameter int FIFO_DEPTH   = 0,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if.candidate bench);
  string activity_vcd, activity_window, retire_ledger, candidate_name;
  integer window_fd, ledger_fd, retire_ordinal, lane;
  time window_start, window_end;

`ifdef K2_POSTROUTE_FOVEA_CORE
  logic native_rst, native_valid, native_ack;
  logic [15:0] native_req, native_ack_mask;
  logic [3:0] native_addr;
  logic reset_quiet_armed = 1'b0;

  assign native_rst = ~bench.rst_n;
  always_comb begin
    native_ack_mask = '0;
    if (native_valid && !$isunknown(native_addr) &&
        bench.source_valid[native_addr])
      native_ack_mask[native_addr] = 1'b1;
  end
  assign native_req = bench.source_valid & ~native_ack_mask;
  assign native_ack = |native_ack_mask;

  `K2_POSTROUTE_DUT dut (
    .clk(bench.clk), .rst(native_rst), .req(native_req),
    .valid(native_valid), .addr(native_addr)
  );

  always_comb begin
    bench.source_ready = '0;
    bench.retire_valid = '0;
    for (lane = 0; lane < RETIRE_LANES; lane = lane + 1) begin
      bench.retire_event[lane] = '0;
      bench.retire_source[lane] = '0;
    end
    if (native_ack) begin
      bench.source_ready[native_addr] = 1'b1;
      bench.retire_valid[0] = 1'b1;
      bench.retire_event[0] = ADDR_WIDTH'(native_addr);
      bench.retire_source[0] = SOURCE_WIDTH'(native_addr);
    end
  end

  always @(posedge bench.clk) if (bench.rst_n === 1'b1) begin
    if (native_valid && $isunknown(native_addr))
      $fatal(1, "postroute fovea emitted unknown address");
    if (native_valid && !$isunknown(native_addr) &&
        !bench.source_valid[native_addr])
      $fatal(1, "postroute fovea duplicate/phantom result");
  end
  always @(posedge bench.clk or posedge bench.rst_n)
    if (bench.rst_n) reset_quiet_armed <= 1'b0;
    else reset_quiet_armed <= 1'b1;
  always @(negedge bench.clk)
    if (!bench.rst_n && reset_quiet_armed && (native_valid !== 1'b0))
      $fatal(1, "postroute fovea native valid active during reset");
`elsif K2_POSTROUTE_CLUSTER2_CORE
  logic native_rst, native_valid0, native_valid1;
  logic [15:0] native_req, native_result_mask, native_ack_mask;
  logic [1:0] native_row0, native_row1;
  logic [3:0] native_col_mask0, native_col_mask1;
  logic reset_quiet_armed = 1'b0;
  integer column, source;

  assign native_rst = ~bench.rst_n;
  always_comb begin
    native_result_mask = '0;
    if (native_valid0 && !$isunknown({native_row0, native_col_mask0}))
      for (column = 0; column < 4; column = column + 1)
        if (native_col_mask0[column])
          native_result_mask[(integer'(native_row0) * 4) + column] = 1'b1;
    if (native_valid1 && !$isunknown({native_row1, native_col_mask1}))
      for (column = 0; column < 4; column = column + 1)
        if (native_col_mask1[column])
          native_result_mask[(integer'(native_row1) * 4) + column] = 1'b1;
  end
  assign native_ack_mask = native_result_mask & bench.source_valid;
  assign native_req = bench.source_valid & ~native_ack_mask;

  `K2_POSTROUTE_DUT dut (
    .clk(bench.clk), .rst(native_rst), .req(native_req),
    .valid0(native_valid0), .row0(native_row0),
    .col_mask0(native_col_mask0), .valid1(native_valid1),
    .row1(native_row1), .col_mask1(native_col_mask1)
  );

  always_comb begin
    bench.source_ready = native_ack_mask;
    bench.retire_valid = '0;
    for (lane = 0; lane < RETIRE_LANES; lane = lane + 1) begin
      bench.retire_event[lane] = '0;
      bench.retire_source[lane] = '0;
    end
    for (column = 0; column < 4; column = column + 1) begin
      source = (integer'(native_row0) * 4) + column;
      if (native_valid0 && !$isunknown({native_row0, native_col_mask0}) &&
          native_col_mask0[column]) begin
        bench.retire_valid[column] = 1'b1;
        bench.retire_event[column] = ADDR_WIDTH'(source);
        bench.retire_source[column] = SOURCE_WIDTH'(source);
      end
      source = (integer'(native_row1) * 4) + column;
      if (native_valid1 && !$isunknown({native_row1, native_col_mask1}) &&
          native_col_mask1[column]) begin
        bench.retire_valid[4 + column] = 1'b1;
        bench.retire_event[4 + column] = ADDR_WIDTH'(source);
        bench.retire_source[4 + column] = SOURCE_WIDTH'(source);
      end
    end
  end

  always @(posedge bench.clk) if (bench.rst_n === 1'b1) begin
    if ((native_valid0 && $isunknown({native_row0, native_col_mask0})) ||
        (native_valid1 && $isunknown({native_row1, native_col_mask1})))
      $fatal(1, "postroute cluster2 emitted unknown result");
    if ((native_result_mask & ~bench.source_valid) != '0)
      $fatal(1, "postroute cluster2 duplicate/phantom result");
  end
  always @(posedge bench.clk or posedge bench.rst_n)
    if (bench.rst_n) reset_quiet_armed <= 1'b0;
    else reset_quiet_armed <= 1'b1;
  always @(negedge bench.clk)
    if (!bench.rst_n && reset_quiet_armed &&
        ((native_valid0 !== 1'b0) || (native_valid1 !== 1'b0)))
      $fatal(1, "postroute cluster2 native valid active during reset");
`else
  logic sample_clk_i = 1'b0;
  logic [15:0] source_accept_o;
  logic link_clk_o;
`ifdef K2_POSTROUTE_R1
  logic [1:0] link_data_o;
`else
  logic [4:0] link_data_o;
`endif
  logic [1:0] retire_valid_o;
  logic [3:0] retire_addr0_o, retire_addr1_o;
  logic drain_idle_o, protocol_error_o;

  initial begin
    #`K2_POSTROUTE_SAMPLE_FIRST_RISE_PS sample_clk_i = 1'b1;
    forever #`K2_POSTROUTE_REF_HALF_PS sample_clk_i = ~sample_clk_i;
  end

  `K2_POSTROUTE_DUT dut (
    .ref_clk_i(bench.clk), .sample_clk_i(sample_clk_i), .rst_n(bench.rst_n),
    .source_pending_i(bench.source_valid), .source_accept_o(source_accept_o),
    .link_clk_o(link_clk_o), .link_data_o(link_data_o),
    .retire_valid_o(retire_valid_o), .retire_addr0_o(retire_addr0_o),
    .retire_addr1_o(retire_addr1_o), .drain_idle_o(drain_idle_o),
    .protocol_error_o(protocol_error_o)
  );

  always_comb begin
    bench.source_ready = '0;
    bench.retire_valid = '0;
    for (lane = 0; lane < RETIRE_LANES; lane = lane + 1) begin
      bench.retire_event[lane] = '0;
      bench.retire_source[lane] = '0;
    end
    if (bench.rst_n === 1'b1) begin
      bench.source_ready = source_accept_o;
      bench.retire_valid = retire_valid_o;
      bench.retire_event[0] = ADDR_WIDTH'(retire_addr0_o);
      bench.retire_event[1] = ADDR_WIDTH'(retire_addr1_o);
      bench.retire_source[0] = SOURCE_WIDTH'(retire_addr0_o);
      bench.retire_source[1] = SOURCE_WIDTH'(retire_addr1_o);
    end
  end

  always @(posedge bench.clk) if (bench.rst_n === 1'b1) begin
    if (protocol_error_o !== 1'b0)
      $fatal(1, "postroute staged endpoint protocol error or unknown");
    if ($isunknown({source_accept_o, retire_valid_o, retire_addr0_o,
                    retire_addr1_o, drain_idle_o}))
      $fatal(1, "postroute staged endpoint emitted unknown result");
  end
`endif

  // SDF is applied to the one stable DUT scope before reset or stimulus.
  initial begin
`ifdef K2_POSTROUTE_SDF
    $sdf_annotate(`K2_POSTROUTE_SDF, dut);
    $display("K2_POSTROUTE_SDF_REQUESTED scope=aer_clean_tb.candidate.dut");
`else
    $fatal(2, "K2_POSTROUTE_SDF must be defined");
`endif
  end

  // VCD and the independent retirement ledger use exactly the frozen common
  // TB measurement interval. Reset/warm-up and candidate-dependent drain are
  // therefore excluded from power annotation.
  initial begin
    if (!$value$plusargs("ACTIVITY_VCD=%s", activity_vcd) ||
        !$value$plusargs("ACTIVITY_WINDOW=%s", activity_window) ||
        !$value$plusargs("RETIRE_LEDGER=%s", retire_ledger) ||
        !$value$plusargs("CANDIDATE=%s", candidate_name))
      $fatal(2, "missing postroute activity output plusarg");
    window_fd = $fopen(activity_window, "w");
    ledger_fd = $fopen(retire_ledger, "w");
    if ((window_fd == 0) || (ledger_fd == 0))
      $fatal(2, "cannot create postroute activity ledger");
    $fdisplay(ledger_fd,
      "ordinal\tsim_tick_1ps\tlane\tlogical_source\tlogical_event");
    retire_ordinal = 0;
    $dumpfile(activity_vcd);
    wait (aer_clean_tb.measurement_active === 1'b1);
    window_start = $time;
    $dumpvars(0, dut);
    $dumpon;
    wait (aer_clean_tb.measurement_active === 1'b0);
    window_end = $time;
    $dumpoff;
    $dumpflush;
    $fdisplay(window_fd, "candidate=%s", candidate_name);
    $fdisplay(window_fd, "start_tick_1ps=%0t", window_start);
    $fdisplay(window_fd, "end_tick_1ps=%0t", window_end);
    $fdisplay(window_fd, "ref_period_ps=%0d", `K2_POSTROUTE_PERIOD_PS);
    $fdisplay(window_fd, "sample_period_ps=%0d", `K2_POSTROUTE_PERIOD_PS);
    $fdisplay(window_fd, "sample_first_rise_ps=%0d",
              `K2_POSTROUTE_SAMPLE_FIRST_RISE_PS);
    $fdisplay(window_fd, "scope=aer_clean_tb.candidate.dut");
    $fclose(window_fd);
    $fclose(ledger_fd);
  end

  always @(posedge bench.clk) begin
    if (aer_clean_tb.measurement_active === 1'b1)
      for (integer record_lane = 0; record_lane < RETIRE_LANES;
           record_lane = record_lane + 1)
        if (bench.retire_valid[record_lane] && bench.retire_ready[record_lane]) begin
          if ($isunknown({bench.retire_source[record_lane],
                          bench.retire_event[record_lane]}))
            $fatal(1, "unknown retirement inside activity window");
          $fdisplay(ledger_fd, "%0d\t%0t\t%0d\t%0d\t%0h",
                    retire_ordinal, $time, record_lane,
                    bench.retire_source[record_lane],
                    bench.retire_event[record_lane]);
          retire_ordinal = retire_ordinal + 1;
        end
  end

  initial begin
    if (NUM_SOURCES != 16 || ADDR_WIDTH != 16 || FIFO_DEPTH != 0)
      $fatal(2, "postroute common activity requires N16/A16/FIFO0");
`ifdef K2_POSTROUTE_FOVEA_CORE
    if (RETIRE_LANES != 1) $fatal(2, "fovea requires one retire lane");
`elsif K2_POSTROUTE_CLUSTER2_CORE
    if (RETIRE_LANES != 8) $fatal(2, "cluster2 requires eight retire lanes");
`else
    if (RETIRE_LANES != 2) $fatal(2, "staged endpoints require two retire lanes");
`endif
  end
endmodule
