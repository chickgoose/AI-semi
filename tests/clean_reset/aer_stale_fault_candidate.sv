// Test-only negative control. It serves the first epoch normally, remembers
// that traffic was observed, and emits pre-reset address zero after the second
// reset is released. The disjoint post-reset address set must reject it as a
// stale phantom before legitimate post-reset traffic begins.
module aer_stale_fault_candidate #(
  parameter int NUM_SOURCES = 4,
  parameter int ADDR_WIDTH = 16,
  parameter int RETIRE_LANES = 2,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if.candidate bench);
  logic observed_traffic = 1'b0;
  logic stale_armed = 1'b0;
  integer source;
  integer selected_source;
  integer lane;

  // Deliberately retain test-control state across reset so this model can
  // inject a cross-epoch protocol fault. This module is never candidate RTL.
  always @(posedge bench.clk or negedge bench.rst_n) begin
    if (!bench.rst_n) begin
      if (observed_traffic)
        stale_armed <= 1'b1;
    end else if (|bench.source_valid) begin
      observed_traffic <= 1'b1;
    end
  end

  always_comb begin
    selected_source = -1;
    for (source = 0; source < NUM_SOURCES; source = source + 1)
      if ((selected_source < 0) && bench.source_valid[source])
        selected_source = source;

    bench.source_ready = '0;
    bench.retire_valid = '0;
    for (lane = 0; lane < RETIRE_LANES; lane = lane + 1) begin
      bench.retire_event[lane] = '0;
      bench.retire_source[lane] = '0;
    end

    if (bench.rst_n && stale_armed) begin
      bench.retire_valid[0] = 1'b1;
      bench.retire_event[0] = '0;
      bench.retire_source[0] = '0;
    end else if (bench.rst_n && (selected_source >= 0)) begin
      bench.source_ready[selected_source] = 1'b1;
      bench.retire_valid[0] = 1'b1;
      bench.retire_event[0] = ADDR_WIDTH'(selected_source);
      bench.retire_source[0] = SOURCE_WIDTH'(selected_source);
    end
  end
endmodule
