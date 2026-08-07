`timescale 1ns/1ps

module a5_transition_predictor #(
  parameter int NUM_SOURCES = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES),
  parameter int HISTORY_BITS = SOURCE_WIDTH,
  parameter int TABLE_ENTRIES = NUM_SOURCES,
  parameter int CONF_WIDTH = 2,
  parameter logic [CONF_WIDTH-1:0] USE_THRESHOLD =
    (1 << (CONF_WIDTH-1))
) (
  input  logic clk,
  input  logic rst_n,

  input  logic lookup_valid,
  input  logic [SOURCE_WIDTH-1:0] lookup_context,
  output logic prediction_valid,
  output logic [SOURCE_WIDTH-1:0] prediction_target,
  output logic [CONF_WIDTH-1:0] prediction_confidence,

  input  logic update_valid,
  input  logic [SOURCE_WIDTH-1:0] update_context,
  input  logic [SOURCE_WIDTH-1:0] update_actual
);
  localparam logic [CONF_WIDTH-1:0] CONF_MAX = {CONF_WIDTH{1'b1}};
  localparam logic [CONF_WIDTH-1:0] CONF_WEAK = {{(CONF_WIDTH-1){1'b0}}, 1'b1};
  localparam int INDEX_WIDTH =
    (TABLE_ENTRIES <= 1) ? 1 : $clog2(TABLE_ENTRIES);

  logic entry_valid [TABLE_ENTRIES];
  logic [HISTORY_BITS-1:0] entry_tag [TABLE_ENTRIES];
  logic [SOURCE_WIDTH-1:0] entry_target [TABLE_ENTRIES];
  logic [CONF_WIDTH-1:0] entry_confidence [TABLE_ENTRIES];
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
      $fatal(1, "A5 predictor HISTORY_BITS must be in [1,SOURCE_WIDTH]");
    if (TABLE_ENTRIES < 1)
      $fatal(1, "A5 predictor TABLE_ENTRIES must be positive");
    if (CONF_WIDTH < 1)
      $fatal(1, "A5 predictor CONF_WIDTH must be positive");
  end

  always_comb begin
    lookup_tag = lookup_context[HISTORY_BITS-1:0];
    update_tag = update_context[HISTORY_BITS-1:0];
    lookup_index = table_index(lookup_tag);
    update_index = table_index(update_tag);
  end

  always_comb begin
    prediction_valid = 1'b0;
    prediction_target = '0;
    prediction_confidence = '0;
    if (lookup_valid && (int'(lookup_context) < NUM_SOURCES)) begin
      prediction_target = entry_target[lookup_index];
      prediction_confidence = entry_confidence[lookup_index];
      prediction_valid = entry_valid[lookup_index] &&
                         (entry_tag[lookup_index] == lookup_tag) &&
                         (entry_confidence[lookup_index] >= USE_THRESHOLD);
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (reset_index = 0; reset_index < TABLE_ENTRIES;
           reset_index = reset_index + 1) begin
        entry_valid[reset_index] <= 1'b0;
        entry_tag[reset_index] <= '0;
        entry_target[reset_index] <= '0;
        entry_confidence[reset_index] <= '0;
      end
    end else if (update_valid && (int'(update_context) < NUM_SOURCES) &&
                 (int'(update_actual) < NUM_SOURCES)) begin
      if (!entry_valid[update_index] ||
          (entry_tag[update_index] != update_tag)) begin
        entry_valid[update_index] <= 1'b1;
        entry_tag[update_index] <= update_tag;
        entry_target[update_index] <= update_actual;
        entry_confidence[update_index] <= CONF_WEAK;
      end else if (entry_target[update_index] == update_actual) begin
        if (entry_confidence[update_index] != CONF_MAX)
          entry_confidence[update_index] <=
            entry_confidence[update_index] + 1'b1;
      end else if (entry_confidence[update_index] != '0) begin
        entry_confidence[update_index] <=
          entry_confidence[update_index] - 1'b1;
      end else begin
        entry_target[update_index] <= update_actual;
        entry_confidence[update_index] <= CONF_WEAK;
      end
    end
  end
endmodule
