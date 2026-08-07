`timescale 1ns/1ps

// Candidate-owned replacement for the common TB's replaceable candidate cell.
// It deliberately has the expected module name so the frozen TB is compiled
// byte-for-byte without its historical adapter implementation.
module aer_legacy_candidate_adapter #(
  parameter int NUM_SOURCES  = 16,
  parameter int ADDR_WIDTH   = 16,
  parameter int RETIRE_LANES = 4,
  parameter int FIFO_DEPTH   = 4,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if.candidate bench);
  a7_parallel_event_compactor_binding #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(RETIRE_LANES),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) a7_binding(bench);
endmodule
