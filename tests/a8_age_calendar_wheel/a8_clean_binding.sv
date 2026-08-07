// Candidate-specific, storage-free shim for the fixed common-TB instantiation
// seam. This file is compiled instead of tb/clean/aer_legacy_candidate_adapter.sv.
`ifndef A8_BUCKET_CYCLES
`define A8_BUCKET_CYCLES 4
`endif
`ifndef A8_EPOCH_COUNT
`define A8_EPOCH_COUNT 8
`endif

module aer_legacy_candidate_adapter #(
  parameter int NUM_SOURCES  = 16,
  parameter int ADDR_WIDTH   = 16,
  parameter int RETIRE_LANES = 2,
  parameter int FIFO_DEPTH   = 4,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if.candidate bench);
  logic native_retire_valid;
  logic [ADDR_WIDTH-1:0] native_retire_event;
  logic [SOURCE_WIDTH-1:0] native_retire_source;
  integer lane;

`ifdef A8_REFERENCE_EXACT
  a8_exact_age_reference #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) candidate (
    .clk(bench.clk),
    .rst_n(bench.rst_n),
    .source_valid(bench.source_valid),
    .source_event(bench.source_event),
    .source_ready(bench.source_ready),
    .retire_valid(native_retire_valid),
    .retire_event(native_retire_event),
    .retire_source(native_retire_source)
  );
`elsif A8_REFERENCE_RR
  a8_rr_reference #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) candidate (
    .clk(bench.clk),
    .rst_n(bench.rst_n),
    .source_valid(bench.source_valid),
    .source_event(bench.source_event),
    .source_ready(bench.source_ready),
    .retire_valid(native_retire_valid),
    .retire_event(native_retire_event),
    .retire_source(native_retire_source)
  );
`else
  a8_age_calendar_wheel #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .BUCKET_CYCLES(`A8_BUCKET_CYCLES),
    .EPOCH_COUNT(`A8_EPOCH_COUNT),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) candidate (
    .clk(bench.clk),
    .rst_n(bench.rst_n),
    .source_valid(bench.source_valid),
    .source_event(bench.source_event),
    .source_ready(bench.source_ready),
    .retire_valid(native_retire_valid),
    .retire_event(native_retire_event),
    .retire_source(native_retire_source)
  );
`endif

  always_comb begin
    bench.retire_valid = '0;
    for (lane = 0; lane < RETIRE_LANES; lane = lane + 1) begin
      bench.retire_event[lane] = '0;
      bench.retire_source[lane] = '0;
    end
    bench.retire_valid[0] = native_retire_valid;
    bench.retire_event[0] = native_retire_event;
    bench.retire_source[0] = native_retire_source;
  end

  logic unused_retire_ready;
  assign unused_retire_ready = bench.retire_ready[0];
endmodule
