`timescale 1ns/1ps

// Storage-free normalized binding for the candidate-only adversarial reference.
module a7_replicated_selector_binding #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int RETIRE_LANES = 4,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if.candidate bench);
  logic [NUM_SOURCES-1:0][ADDR_WIDTH-1:0] packed_source_event;
  logic [RETIRE_LANES-1:0][ADDR_WIDTH-1:0] packed_retire_event;
  logic [RETIRE_LANES-1:0][SOURCE_WIDTH-1:0] packed_retire_source;
  genvar item;
  generate
    for (item = 0; item < NUM_SOURCES; item = item + 1)
      always_comb packed_source_event[item] = bench.source_event[item];
    for (item = 0; item < RETIRE_LANES; item = item + 1) begin
      always_comb bench.retire_event[item] = packed_retire_event[item];
      always_comb bench.retire_source[item] = packed_retire_source[item];
    end
  endgenerate
  a7_replicated_selector_reference #(
    .NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(RETIRE_LANES), .SOURCE_WIDTH(SOURCE_WIDTH)
  ) candidate (
    .clk(bench.clk), .rst_n(bench.rst_n),
    .source_valid(bench.source_valid), .source_event(packed_source_event),
    .source_ready(bench.source_ready), .retire_valid(bench.retire_valid),
    .retire_event(packed_retire_event), .retire_source(packed_retire_source),
    .retire_ready(bench.retire_ready)
  );
endmodule
