module a2_k2_official_common_binding #(
    parameter int NUM_SOURCES = 16,
    parameter int ADDR_WIDTH = 16,
    parameter int RETIRE_LANES = 2,
    parameter int FIFO_DEPTH = 0
) (
    aer_bench_if.candidate bench
);
`ifndef SYNTHESIS
  initial begin
    if (RETIRE_LANES != 2)
      $fatal(1, "A2_K2_CONFIG_RETIRE_LANES expected=2 got=%0d", RETIRE_LANES);
    if (FIFO_DEPTH != 0)
      $fatal(1, "A2_K2_CONFIG_STORAGE_FREE expected FIFO_DEPTH=0 got=%0d",
             FIFO_DEPTH);
  end
`endif

  logic drain_idle_unused;
  a2_k2_official_always_ready_wrapper #(
      .NUM_SOURCES(NUM_SOURCES),
      .ADDR_WIDTH(ADDR_WIDTH),
      .OFFICIAL_ALWAYS_READY(1'b1)
  ) normalized (
      .clk(bench.clk),
      .rst_n(bench.rst_n),
      .source_valid(bench.source_valid),
      .source_event(bench.source_event),
      .source_ready(bench.source_ready),
      .retire_ready(bench.retire_ready),
      .retire_valid(bench.retire_valid),
      .retire_event(bench.retire_event),
      .retire_source(bench.retire_source),
      .drain_idle(drain_idle_unused)
  );
endmodule

// Frozen aer_clean_tb selects this pre-existing seam name under
// AER_CLEAN_GANGHEE_NATIVE.  The alias adds no logic or state.
module aer_ganghee_native_binding #(
    parameter int NUM_SOURCES = 16,
    parameter int ADDR_WIDTH = 16,
    parameter int RETIRE_LANES = 2,
    parameter int FIFO_DEPTH = 0
) (
    aer_bench_if.candidate bench
);
  a2_k2_official_common_binding #(
      .NUM_SOURCES(NUM_SOURCES),
      .ADDR_WIDTH(ADDR_WIDTH),
      .RETIRE_LANES(RETIRE_LANES),
      .FIFO_DEPTH(FIFO_DEPTH)
  ) implementation (bench);
endmodule
