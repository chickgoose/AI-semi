`timescale 1ns/1ps

`ifndef A5_BIND_ENABLE_PREDICTOR
`define A5_BIND_ENABLE_PREDICTOR 1
`endif
`ifndef A5_BIND_HISTORY_BITS
`define A5_BIND_HISTORY_BITS SOURCE_WIDTH
`endif
`ifndef A5_BIND_TABLE_ENTRIES
`define A5_BIND_TABLE_ENTRIES NUM_SOURCES
`endif
`ifndef A5_BIND_CONF_WIDTH
`define A5_BIND_CONF_WIDTH 2
`endif

// Candidate-only normalized binding.  It is a wire-level port map and adds no
// arbitration, buffering, retry, or event reconstruction outside the DUT.
/* verilator lint_off DECLFILENAME */
/* verilator lint_off UNUSEDPARAM */
module aer_ganghee_native_binding #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int RETIRE_LANES = 1,
  parameter int FIFO_DEPTH = 4,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if.candidate bench);
  logic retire_valid;
  logic [ADDR_WIDTH-1:0] retire_event;
  logic [SOURCE_WIDTH-1:0] retire_source;
  logic [31:0] prediction_attempts;
  logic [31:0] prediction_hits;
  logic [31:0] prediction_misses;
  logic [31:0] confidence_fallbacks;
  logic [31:0] fairness_fallbacks;
  integer lane;

  initial begin
    if (RETIRE_LANES != 1)
      $fatal(1, "A5 binding requires RETIRE_LANES=1");
  end

  a5_speculative_pregrant_core #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH),
    .ENABLE_PREDICTOR(`A5_BIND_ENABLE_PREDICTOR),
    .PRED_HISTORY_BITS(`A5_BIND_HISTORY_BITS),
    .PRED_TABLE_ENTRIES(`A5_BIND_TABLE_ENTRIES),
    .PRED_CONF_WIDTH(`A5_BIND_CONF_WIDTH),
    .MAX_PREDICT_STREAK(3)
  ) candidate (
    .clk(bench.clk),
    .rst_n(bench.rst_n),
    .source_valid(bench.source_valid),
    .source_event(bench.source_event),
    .source_ready(bench.source_ready),
    .retire_valid,
    .retire_ready(bench.retire_ready[0]),
    .retire_event,
    .retire_source,
    .prediction_attempts,
    .prediction_hits,
    .prediction_misses,
    .confidence_fallbacks,
    .fairness_fallbacks
  );

  always_comb begin
    bench.retire_valid = '0;
    for (lane = 0; lane < RETIRE_LANES; lane = lane + 1) begin
      bench.retire_event[lane] = '0;
      bench.retire_source[lane] = '0;
    end
    bench.retire_valid[0] = retire_valid;
    bench.retire_event[0] = retire_event;
    bench.retire_source[0] = retire_source;
  end

  final begin
    $display("A5_PREDICTOR_METRICS attempts=%0d hits=%0d misses=%0d confidence_fallbacks=%0d fairness_fallbacks=%0d",
      prediction_attempts, prediction_hits, prediction_misses,
      confidence_fallbacks, fairness_fallbacks);
  end
endmodule
/* verilator lint_on UNUSEDPARAM */
/* verilator lint_on DECLFILENAME */
