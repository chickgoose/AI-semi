`timescale 1ns/1ps

// Storage-free normalized binding. All buffering and arbitration are in A7 RTL.
module a7_parallel_event_compactor_binding #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int RETIRE_LANES = 4,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if.candidate bench);
  a7_parallel_event_compactor #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(RETIRE_LANES),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) candidate (
    .clk(bench.clk),
    .rst_n(bench.rst_n),
    .source_valid(bench.source_valid),
    .source_event(bench.source_event),
    .source_ready(bench.source_ready),
    .retire_valid(bench.retire_valid),
    .retire_event(bench.retire_event),
    .retire_source(bench.retire_source),
    .retire_ready(bench.retire_ready)
  );
endmodule
