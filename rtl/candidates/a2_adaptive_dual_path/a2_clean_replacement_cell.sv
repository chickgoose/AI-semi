`timescale 1ns/1ps

// Candidate-private replacement for the common TB's replaceable compatibility
// cell. The A2 runner omits the historical adapter source and compiles this
// module with the identical signature. It adds no state or behavior.
module aer_legacy_candidate_adapter #(
  parameter int NUM_SOURCES = 4,
  parameter int ADDR_WIDTH = 16,
  parameter int RETIRE_LANES = 2,
  parameter int FIFO_DEPTH = 4,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if bench);
  a2_adaptive_dual_path_binding #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(RETIRE_LANES),
    .RESERVOIR_DEPTH(8),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) candidate(bench);
endmodule
