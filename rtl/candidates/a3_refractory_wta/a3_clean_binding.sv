// Candidate-only storage-free binding for the A3 refractory-WTA salvage.
module aer_legacy_candidate_adapter #(
  parameter int NUM_SOURCES  = 16,
  parameter int ADDR_WIDTH   = 16,
  parameter int RETIRE_LANES = 1,
  parameter int FIFO_DEPTH   = 4,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if.candidate bench);
  logic native_retire_valid;
  logic [ADDR_WIDTH-1:0] native_retire_event;
  logic [SOURCE_WIDTH-1:0] native_retire_source;
  integer lane;

  a3_refractory_wta #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) native_candidate (
    .clk(bench.clk),
    .rst_n(bench.rst_n),
    .source_valid(bench.source_valid),
    .source_ready(bench.source_ready),
    .source_event(bench.source_event),
    .retire_valid(native_retire_valid),
    .retire_ready(bench.retire_ready[0]),
    .retire_event(native_retire_event),
    .retire_source(native_retire_source)
  );

`ifndef SYNTHESIS
  string activity_vcd_path;
  initial begin
    if ($value$plusargs("A3R_VCD=%s", activity_vcd_path)) begin
      $dumpfile(activity_vcd_path);
      $dumpvars(0, native_candidate);
    end
  end
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

  // Compatibility parameters add no binding storage.
  logic unused;
  always_comb unused = (FIFO_DEPTH == 0);
endmodule
