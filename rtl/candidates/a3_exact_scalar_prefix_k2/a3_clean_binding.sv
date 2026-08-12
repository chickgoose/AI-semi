// Storage-free compatibility binding for the frozen common clean TB.  All
// policy, event holding, compaction, and backpressure state is instantiated in
// a3_k2_common_wrapper and is therefore charged as candidate RTL.
module aer_legacy_candidate_adapter #(
  parameter int NUM_SOURCES  = 16,
  parameter int ADDR_WIDTH   = 16,
  parameter int RETIRE_LANES = 2,
  parameter int FIFO_DEPTH   = 4,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if.candidate bench);
  a3_k2_common_wrapper #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) native_candidate (
    .clk(bench.clk),
    .rst_n(bench.rst_n),
    .source_valid(bench.source_valid),
    .source_ready(bench.source_ready),
    .source_event(bench.source_event),
    .retire_valid(bench.retire_valid),
    .retire_ready(bench.retire_ready),
    .retire_event(bench.retire_event),
    .retire_source(bench.retire_source)
  );

`ifndef SYNTHESIS
  initial begin
    if (RETIRE_LANES != 2)
      $fatal(1, "A3_K2_BINDING requires RETIRE_LANES=2");
    if (FIFO_DEPTH < 0)
      $fatal(1, "A3_K2_BINDING FIFO_DEPTH is compatibility-only");
  end
`endif
endmodule
