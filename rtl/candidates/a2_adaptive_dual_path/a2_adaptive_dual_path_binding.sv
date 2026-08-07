`timescale 1ns/1ps

// Storage-free mapping from the A2 native ports to the normalized benchmark
// seam. All arbitration, buffering, ordering, and mode state live in the core.
module a2_adaptive_dual_path_binding #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int RETIRE_LANES = 2,
  parameter int RESERVOIR_DEPTH = 8,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if bench);
  logic native_retire_valid;
  logic [ADDR_WIDTH-1:0] native_retire_event;
  logic [SOURCE_WIDTH-1:0] native_retire_source;
  integer lane;

  a2_adaptive_dual_path_core #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RESERVOIR_DEPTH(RESERVOIR_DEPTH)
  ) core (
    .clk_i(bench.clk),
    .rst_ni(bench.rst_n),
    .source_valid_i(bench.source_valid),
    .source_ready_o(bench.source_ready),
    .source_event_i(bench.source_event),
    .retire_valid_o(native_retire_valid),
    .retire_ready_i(bench.retire_ready[0]),
    .retire_event_o(native_retire_event),
    .retire_source_o(native_retire_source)
  );

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
endmodule
