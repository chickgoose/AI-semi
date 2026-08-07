`timescale 1ns/1ps

// Synthesizable flattened-pin boundary for physical screening.  Predictor
// metrics are disabled so test-only counters are not charged to the candidate.
module a5_speculative_pregrant_ppa_top #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (
  input  logic clk,
  input  logic rst_n,
  input  logic [NUM_SOURCES-1:0] source_valid,
  input  logic [NUM_SOURCES*ADDR_WIDTH-1:0] source_event_flat,
  output logic [NUM_SOURCES-1:0] source_ready,
  output logic retire_valid,
  input  logic retire_ready,
  output logic [ADDR_WIDTH-1:0] retire_event,
  output logic [SOURCE_WIDTH-1:0] retire_source
);
  logic [ADDR_WIDTH-1:0] source_event [NUM_SOURCES];
  logic [31:0] unused_prediction_attempts;
  logic [31:0] unused_prediction_hits;
  logic [31:0] unused_prediction_misses;
  logic [31:0] unused_confidence_fallbacks;
  logic [31:0] unused_fairness_fallbacks;
  integer source;

  always_comb begin
    for (source = 0; source < NUM_SOURCES; source = source + 1)
      source_event[source] =
        source_event_flat[source*ADDR_WIDTH +: ADDR_WIDTH];
  end

  a5_speculative_pregrant_core #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH),
    .ENABLE_PREDICTOR(1'b1),
    .PRED_HISTORY_BITS(SOURCE_WIDTH),
    .PRED_TABLE_ENTRIES(NUM_SOURCES),
    .PRED_CONF_WIDTH(2),
    .ENABLE_METRICS(1'b0),
    .MAX_PREDICT_STREAK(3)
  ) core (
    .clk,
    .rst_n,
    .source_valid,
    .source_event,
    .source_ready,
    .retire_valid,
    .retire_ready,
    .retire_event,
    .retire_source,
    .prediction_attempts(unused_prediction_attempts),
    .prediction_hits(unused_prediction_hits),
    .prediction_misses(unused_prediction_misses),
    .confidence_fallbacks(unused_confidence_fallbacks),
    .fairness_fallbacks(unused_fairness_fallbacks)
  );
endmodule
