// Candidate-only storage-free binding.  The frozen common TB instantiates its
// replaceable cell using this compatibility module name.  All sequential state,
// output holding, and arbitration live in a3_homeostatic_inhibition.
`ifndef A3_URGENCY_WIDTH
`define A3_URGENCY_WIDTH 6
`endif
`ifndef A3_LEAK
`define A3_LEAK 1
`endif
`ifndef A3_GAIN_LOW
`define A3_GAIN_LOW 6
`endif
`ifndef A3_GAIN_HIGH
`define A3_GAIN_HIGH 5
`endif
`ifndef A3_INHIBIT_LOW
`define A3_INHIBIT_LOW 1
`endif
`ifndef A3_INHIBIT_HIGH
`define A3_INHIBIT_HIGH 2
`endif
`ifndef A3_THRESHOLD_BASE
`define A3_THRESHOLD_BASE 8
`endif
`ifndef A3_THRESHOLD_SHIFT
`define A3_THRESHOLD_SHIFT 1
`endif

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

  a3_homeostatic_inhibition #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH),
    .URGENCY_WIDTH(`A3_URGENCY_WIDTH),
    .LEAK(`A3_LEAK),
    .GAIN_LOW_ACTIVITY(`A3_GAIN_LOW),
    .GAIN_HIGH_ACTIVITY(`A3_GAIN_HIGH),
    .INHIBIT_LOW(`A3_INHIBIT_LOW),
    .INHIBIT_HIGH(`A3_INHIBIT_HIGH),
    .THRESHOLD_BASE(`A3_THRESHOLD_BASE),
    .THRESHOLD_SHIFT(`A3_THRESHOLD_SHIFT)
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
    if ($value$plusargs("A3_VCD=%s", activity_vcd_path)) begin
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

  // FIFO_DEPTH is inherited from the replaceable common cell signature and is
  // intentionally unused: this binding adds no free candidate storage.
endmodule

`undef A3_URGENCY_WIDTH
`undef A3_LEAK
`undef A3_GAIN_LOW
`undef A3_GAIN_HIGH
`undef A3_INHIBIT_LOW
`undef A3_INHIBIT_HIGH
`undef A3_THRESHOLD_BASE
`undef A3_THRESHOLD_SHIFT
