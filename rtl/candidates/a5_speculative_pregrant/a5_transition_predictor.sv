`timescale 1ns/1ps

module a5_transition_predictor #(
  parameter int NUM_SOURCES = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES),
  parameter int CONF_WIDTH = 2,
  parameter logic [CONF_WIDTH-1:0] USE_THRESHOLD = 2
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

  logic entry_valid [NUM_SOURCES];
  logic [SOURCE_WIDTH-1:0] entry_target [NUM_SOURCES];
  logic [CONF_WIDTH-1:0] entry_confidence [NUM_SOURCES];
  integer reset_index;

  always_comb begin
    prediction_valid = 1'b0;
    prediction_target = '0;
    prediction_confidence = '0;
    if (lookup_valid && (int'(lookup_context) < NUM_SOURCES)) begin
      prediction_target = entry_target[lookup_context];
      prediction_confidence = entry_confidence[lookup_context];
      prediction_valid = entry_valid[lookup_context] &&
                         (entry_confidence[lookup_context] >= USE_THRESHOLD);
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (reset_index = 0; reset_index < NUM_SOURCES;
           reset_index = reset_index + 1) begin
        entry_valid[reset_index] <= 1'b0;
        entry_target[reset_index] <= '0;
        entry_confidence[reset_index] <= '0;
      end
    end else if (update_valid && (int'(update_context) < NUM_SOURCES) &&
                 (int'(update_actual) < NUM_SOURCES)) begin
      if (!entry_valid[update_context]) begin
        entry_valid[update_context] <= 1'b1;
        entry_target[update_context] <= update_actual;
        entry_confidence[update_context] <= CONF_WEAK;
      end else if (entry_target[update_context] == update_actual) begin
        if (entry_confidence[update_context] != CONF_MAX)
          entry_confidence[update_context] <=
            entry_confidence[update_context] + 1'b1;
      end else if (entry_confidence[update_context] != '0) begin
        entry_confidence[update_context] <=
          entry_confidence[update_context] - 1'b1;
      end else begin
        entry_target[update_context] <= update_actual;
        entry_confidence[update_context] <= CONF_WEAK;
      end
    end
  end
endmodule
