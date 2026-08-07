`timescale 1ns/1ps

// Per-context last observed successor.  This is intentionally distinct from
// both a global last-grant preference and the confidence-gated Markov model.
module a5_last_successor_predictor #(
  parameter int NUM_SOURCES = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES),
  parameter int HISTORY_BITS = SOURCE_WIDTH,
  parameter int TABLE_ENTRIES = NUM_SOURCES
) (
  input logic clk,
  input logic rst_n,
  input logic lookup_valid,
  input logic [SOURCE_WIDTH-1:0] lookup_context,
  output logic prediction_valid,
  output logic [SOURCE_WIDTH-1:0] prediction_target,
  input logic update_valid,
  input logic [SOURCE_WIDTH-1:0] update_context,
  input logic [SOURCE_WIDTH-1:0] update_actual
);
  localparam int INDEX_WIDTH =
    (TABLE_ENTRIES <= 1) ? 1 : $clog2(TABLE_ENTRIES);
  logic entry_valid [TABLE_ENTRIES];
  logic [HISTORY_BITS-1:0] entry_tag [TABLE_ENTRIES];
  logic [SOURCE_WIDTH-1:0] entry_target [TABLE_ENTRIES];
  logic [HISTORY_BITS-1:0] lookup_tag;
  logic [HISTORY_BITS-1:0] update_tag;
  logic [INDEX_WIDTH-1:0] lookup_index;
  logic [INDEX_WIDTH-1:0] update_index;
  integer reset_index;

  function automatic logic [INDEX_WIDTH-1:0] table_index(
    input logic [HISTORY_BITS-1:0] compact_history
  );
    table_index = INDEX_WIDTH'(int'(compact_history) % TABLE_ENTRIES);
  endfunction

  initial begin
    if ((HISTORY_BITS < 1) || (HISTORY_BITS > SOURCE_WIDTH))
      $fatal(1, "A5 last-successor HISTORY_BITS must be in [1,SOURCE_WIDTH]");
    if (TABLE_ENTRIES < 1)
      $fatal(1, "A5 last-successor TABLE_ENTRIES must be positive");
  end

  always_comb begin
    lookup_tag = lookup_context[HISTORY_BITS-1:0];
    update_tag = update_context[HISTORY_BITS-1:0];
    lookup_index = table_index(lookup_tag);
    update_index = table_index(update_tag);
    prediction_target = entry_target[lookup_index];
    prediction_valid = lookup_valid &&
                       (int'(lookup_context) < NUM_SOURCES) &&
                       entry_valid[lookup_index] &&
                       (entry_tag[lookup_index] == lookup_tag);
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (reset_index = 0; reset_index < TABLE_ENTRIES;
           reset_index = reset_index + 1) begin
        entry_valid[reset_index] <= 1'b0;
        entry_tag[reset_index] <= '0;
        entry_target[reset_index] <= '0;
      end
    end else if (update_valid && (int'(update_context) < NUM_SOURCES) &&
                 (int'(update_actual) < NUM_SOURCES)) begin
      entry_valid[update_index] <= 1'b1;
      entry_tag[update_index] <= update_tag;
      entry_target[update_index] <= update_actual;
    end
  end
endmodule
