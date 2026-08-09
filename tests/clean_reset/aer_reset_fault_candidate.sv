// Test-only negative control. Normal traffic is completed combinationally one
// source at a time, but retire_valid is deliberately asserted during reset.
// The mandatory reset checker must reject this candidate.
module aer_reset_fault_candidate #(
  parameter int NUM_SOURCES = 4,
  parameter int ADDR_WIDTH = 16,
  parameter int RETIRE_LANES = 2,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if.candidate bench);
  integer source;
  integer selected_source;
  integer lane;

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

    if (!bench.rst_n) begin
      bench.retire_valid[0] = 1'b1;
    end else if (selected_source >= 0) begin
      bench.source_ready[selected_source] = 1'b1;
      bench.retire_valid[0] = 1'b1;
      bench.retire_event[0] = ADDR_WIDTH'(selected_source);
      bench.retire_source[0] = SOURCE_WIDTH'(selected_source);
    end
  end
endmodule
