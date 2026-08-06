// Compatibility adapter used only to calibrate the clean benchmark with an
// existing single-lane ready/valid candidate.  It is not the clean-slate DUT
// interface and it provides no queueing or event storage.
module aer_legacy_candidate_adapter #(
  parameter int NUM_SOURCES  = 4,
  parameter int ADDR_WIDTH   = 16,
  parameter int RETIRE_LANES = 2,
  parameter int FIFO_DEPTH   = 4,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if.candidate bench);
`ifdef AER_DUT_A23_EE430
`define AER_CLEAN_USE_LEGACY_RV
`elsif AER_DUT_BASELINE
`define AER_CLEAN_USE_LEGACY_RV
`endif

`ifdef AER_CLEAN_USE_LEGACY_RV
  aer_if #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) legacy_bus(bench.clk);

  dut_adapter #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .FIFO_DEPTH(FIFO_DEPTH)
  ) legacy_candidate(legacy_bus);

  integer lane;
  always_comb begin
    legacy_bus.rst_n = bench.rst_n;
    legacy_bus.in_valid = bench.source_valid;
    for (lane = 0; lane < NUM_SOURCES; lane = lane + 1)
      legacy_bus.in_addr[lane] = bench.source_event[lane];
    bench.source_ready = legacy_bus.in_ready;

    bench.retire_valid = '0;
    for (lane = 0; lane < RETIRE_LANES; lane = lane + 1) begin
      bench.retire_event[lane] = '0;
      bench.retire_source[lane] = '0;
    end
    bench.retire_valid[0] = legacy_bus.out_valid;
    bench.retire_event[0] = legacy_bus.out_addr;
    bench.retire_source[0] = legacy_bus.out_src;
    legacy_bus.out_ready = bench.retire_ready[0];
  end

`else
  aer_clean_mock_candidate #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(RETIRE_LANES),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) smoke_candidate(bench);
`endif
`ifdef AER_CLEAN_USE_LEGACY_RV
`undef AER_CLEAN_USE_LEGACY_RV
`endif
endmodule
