`timescale 1ns/1ps

`ifndef A2_RESERVOIR_DEPTH
`define A2_RESERVOIR_DEPTH 8
`endif
`ifndef A2_BANK_COUNT
`define A2_BANK_COUNT 2
`endif
`ifndef A2_ENTER_LEVEL
`define A2_ENTER_LEVEL 4
`endif
`ifndef A2_EXIT_LEVEL
`define A2_EXIT_LEVEL 1
`endif
`ifndef A2_QUIET_CYCLES
`define A2_QUIET_CYCLES 3
`endif

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
    .RESERVOIR_DEPTH(`A2_RESERVOIR_DEPTH),
    .BANK_COUNT(`A2_BANK_COUNT),
    .ENTER_LEVEL(`A2_ENTER_LEVEL),
    .EXIT_LEVEL(`A2_EXIT_LEVEL),
    .QUIET_CYCLES(`A2_QUIET_CYCLES),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) candidate(bench);
endmodule
