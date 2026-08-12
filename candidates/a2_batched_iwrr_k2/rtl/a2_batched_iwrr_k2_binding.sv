`timescale 1ns/1ps

// Storage-free interface binding.  All stateful normalization is instantiated
// inside a2_batched_iwrr_k2_normalized and is therefore charged to the DUT.
module a2_batched_iwrr_k2_binding #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int RETIRE_LANES = 2,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if bench);
  logic drain_idle_unused;
  logic [NUM_SOURCES*ADDR_WIDTH-1:0] source_event_packed;
  logic [RETIRE_LANES*ADDR_WIDTH-1:0] retire_event_packed;
  logic [RETIRE_LANES*SOURCE_WIDTH-1:0] retire_source_packed;
  integer source;
  integer lane;

  always_comb begin
    source_event_packed = '0;
    for (source = 0; source < NUM_SOURCES; source = source + 1)
      source_event_packed[source*ADDR_WIDTH +: ADDR_WIDTH] =
        bench.source_event[source];
    for (lane = 0; lane < RETIRE_LANES; lane = lane + 1) begin
      bench.retire_event[lane] =
        retire_event_packed[lane*ADDR_WIDTH +: ADDR_WIDTH];
      bench.retire_source[lane] =
        retire_source_packed[lane*SOURCE_WIDTH +: SOURCE_WIDTH];
    end
  end

  a2_batched_iwrr_k2_normalized #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(RETIRE_LANES),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) candidate (
    .clk(bench.clk),
    .rst_n(bench.rst_n),
    .source_valid(bench.source_valid),
    .source_ready(bench.source_ready),
    .source_event(source_event_packed),
    .retire_valid(bench.retire_valid),
    .retire_ready(bench.retire_ready),
    .retire_event(retire_event_packed),
    .retire_source(retire_source_packed),
    .drain_idle(drain_idle_unused)
  );
endmodule
