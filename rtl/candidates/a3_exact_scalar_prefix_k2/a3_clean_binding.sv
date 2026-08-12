// Storage-free compatibility binding for the frozen common clean TB.  All
// policy, event holding, compaction, and backpressure state is instantiated in
// a3_k2_common_wrapper and is therefore charged as candidate RTL.
module aer_legacy_candidate_adapter #(
  parameter int NUM_SOURCES  = 16,
  parameter int ADDR_WIDTH   = 16,
  parameter int RETIRE_LANES = 2,
  parameter int FIFO_DEPTH   = 0,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if.candidate bench);
  logic [NUM_SOURCES*ADDR_WIDTH-1:0] source_event_flat;
  logic [ADDR_WIDTH-1:0] native_retire_event0;
  logic [ADDR_WIDTH-1:0] native_retire_event1;
  logic [SOURCE_WIDTH-1:0] native_retire_source0;
  logic [SOURCE_WIDTH-1:0] native_retire_source1;

  genvar source;
  generate
    for (source = 0; source < NUM_SOURCES; source = source + 1) begin : event_pack
      assign source_event_flat[source*ADDR_WIDTH +: ADDR_WIDTH] =
        bench.source_event[source];
    end
  endgenerate

  a3_k2_common_wrapper #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) native_candidate (
    .clk(bench.clk),
    .rst_n(bench.rst_n),
    .source_valid(bench.source_valid),
    .source_ready(bench.source_ready),
    .source_event_flat(source_event_flat),
    .retire_valid(bench.retire_valid),
    .retire_ready(bench.retire_ready),
    .retire_event0(native_retire_event0),
    .retire_event1(native_retire_event1),
    .retire_source0(native_retire_source0),
    .retire_source1(native_retire_source1)
  );

  always_comb begin
    bench.retire_event[0] = native_retire_event0;
    bench.retire_event[1] = native_retire_event1;
    bench.retire_source[0] = native_retire_source0;
    bench.retire_source[1] = native_retire_source1;
  end

`ifndef SYNTHESIS
  initial begin
    if (RETIRE_LANES != 2)
      $fatal(1, "A3_K2_BINDING requires RETIRE_LANES=2");
    if (FIFO_DEPTH != 0)
      $fatal(1, "A3_K2_BINDING requires compatibility FIFO_DEPTH=0");
  end
`endif
endmodule
