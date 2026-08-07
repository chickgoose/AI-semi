// A9-only compile target replacement for the historical adapter module name
// instantiated by the frozen common TB.  The A9 runner excludes the historical
// adapter definition.  This module is strictly a wire-only binding: all event
// storage and arbitration are inside a9_distributed_token_fabric.
/* verilator lint_off DECLFILENAME */
module aer_legacy_candidate_adapter #(
  parameter int NUM_SOURCES  = 16,
  parameter int ADDR_WIDTH   = 16,
  parameter int RETIRE_LANES = 4,
  parameter int FIFO_DEPTH   = 4,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if bench);
  a9_distributed_token_fabric #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(RETIRE_LANES),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) candidate (
    .clk_i(bench.clk),
    .rst_ni(bench.rst_n),
    .source_valid_i(bench.source_valid),
    .source_ready_o(bench.source_ready),
    .source_event_i(bench.source_event),
    .retire_valid_o(bench.retire_valid),
    .retire_ready_i(bench.retire_ready),
    .retire_event_o(bench.retire_event),
    .retire_source_o(bench.retire_source)
  );

  logic unused_fifo_depth;
  assign unused_fifo_depth = (FIFO_DEPTH == 0);
endmodule
/* verilator lint_on DECLFILENAME */
