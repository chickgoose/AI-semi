`timescale 1ns/1ps

module a5_speculative_pregrant_core #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES),
  parameter bit ENABLE_PREDICTOR = 1'b1,
  parameter int PRED_HISTORY_BITS = SOURCE_WIDTH,
  parameter int PRED_TABLE_ENTRIES = NUM_SOURCES,
  parameter int PRED_CONF_WIDTH = 2,
  parameter bit ENABLE_METRICS = 1'b1,
  parameter int MAX_PREDICT_STREAK = 3
) (
  input  logic clk,
  input  logic rst_n,
  input  logic [NUM_SOURCES-1:0] source_valid,
`ifdef A5_YOSYS_PROXY
  input  logic [NUM_SOURCES*ADDR_WIDTH-1:0] source_event_flat,
`else
  input  logic [ADDR_WIDTH-1:0] source_event [NUM_SOURCES],
`endif
  output logic [NUM_SOURCES-1:0] source_ready,
  output logic retire_valid,
  input  logic retire_ready,
  output logic [ADDR_WIDTH-1:0] retire_event,
  output logic [SOURCE_WIDTH-1:0] retire_source,
  output logic [31:0] prediction_attempts,
  output logic [31:0] prediction_hits,
  output logic [31:0] prediction_misses,
  output logic [31:0] confidence_fallbacks,
  output logic [31:0] fairness_fallbacks
);
  localparam int STREAK_WIDTH =
    (MAX_PREDICT_STREAK <= 1) ? 1 : $clog2(MAX_PREDICT_STREAK + 1);

  logic [SOURCE_WIDTH-1:0] fallback_start;
  logic [ADDR_WIDTH-1:0] source_event_local [NUM_SOURCES];
  logic output_valid;
  logic [ADDR_WIDTH-1:0] output_event;
  logic [SOURCE_WIDTH-1:0] output_source;
  logic history_valid;
  logic [SOURCE_WIDTH-1:0] history_source;
  logic [STREAK_WIDTH-1:0] predict_streak;

  logic predictor_valid;
  logic [SOURCE_WIDTH-1:0] predictor_target;
  logic predictor_update;

  logic slot_available;
  logic any_request;
  logic prediction_allowed;
  logic prediction_attempt;
  logic prediction_hit;
  logic prediction_miss;
  logic fairness_fallback;
  logic confidence_fallback;
  logic bypass_hit;
  integer offset;
  integer event_source;
  integer scan_source;
  integer selected_source;

  always_comb begin
    for (event_source = 0; event_source < NUM_SOURCES;
         event_source = event_source + 1) begin
`ifdef A5_YOSYS_PROXY
      source_event_local[event_source] =
        source_event_flat[event_source*ADDR_WIDTH +: ADDR_WIDTH];
`else
      source_event_local[event_source] = source_event[event_source];
`endif
    end
  end

  assign slot_available = !output_valid || retire_ready;
  assign any_request = |source_valid;
  assign prediction_allowed =
    predict_streak < STREAK_WIDTH'(MAX_PREDICT_STREAK);
  assign prediction_attempt = slot_available && any_request &&
                              predictor_valid && prediction_allowed;
  assign prediction_hit = prediction_attempt &&
                          source_valid[predictor_target];
  assign prediction_miss = prediction_attempt &&
                           !source_valid[predictor_target];
  assign fairness_fallback = slot_available && any_request &&
                             predictor_valid && !prediction_allowed;
  assign confidence_fallback = slot_available && any_request &&
                               !prediction_attempt && !fairness_fallback;
  assign bypass_hit = !output_valid && retire_ready && prediction_hit;

  always_comb begin
    selected_source = -1;
    scan_source = 0;
    offset = 0;
    if (slot_available && any_request) begin
      if (prediction_hit) begin
        selected_source = int'(predictor_target);
      end else begin
        for (offset = 0; offset < NUM_SOURCES; offset = offset + 1) begin
          scan_source = int'(fallback_start) + offset;
          if (scan_source >= NUM_SOURCES)
            scan_source = scan_source - NUM_SOURCES;
          if ((selected_source < 0) && source_valid[scan_source])
            selected_source = scan_source;
        end
      end
    end

    source_ready = '0;
    if (selected_source >= 0)
      source_ready[selected_source] = 1'b1;

    retire_valid = output_valid || bypass_hit;
    if (output_valid) begin
      retire_event = output_event;
      retire_source = output_source;
    end else if (bypass_hit) begin
      retire_event = source_event_local[selected_source];
      retire_source = SOURCE_WIDTH'(selected_source);
    end else begin
      retire_event = '0;
      retire_source = '0;
    end
  end

  assign predictor_update = (selected_source >= 0) && history_valid;

  generate
    if (ENABLE_PREDICTOR) begin : predictor_enabled
      a5_transition_predictor #(
        .NUM_SOURCES(NUM_SOURCES),
        .SOURCE_WIDTH(SOURCE_WIDTH),
        .HISTORY_BITS(PRED_HISTORY_BITS),
        .TABLE_ENTRIES(PRED_TABLE_ENTRIES),
        .CONF_WIDTH(PRED_CONF_WIDTH)
      ) predictor (
        .clk,
        .rst_n,
        .lookup_valid(history_valid),
        .lookup_context(history_source),
        .prediction_valid(predictor_valid),
        .prediction_target(predictor_target),
        .prediction_confidence(),
        .update_valid(predictor_update),
        .update_context(history_source),
        .update_actual(SOURCE_WIDTH'(selected_source))
      );
    end else begin : predictor_disabled
      always_comb begin
        predictor_valid = 1'b0;
        predictor_target = '0;
      end
    end
  endgenerate

  generate
    if (ENABLE_METRICS) begin : metrics_enabled
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          prediction_attempts <= '0;
          prediction_hits <= '0;
          prediction_misses <= '0;
          confidence_fallbacks <= '0;
          fairness_fallbacks <= '0;
        end else begin
          if (prediction_attempt)
            prediction_attempts <= prediction_attempts + 1'b1;
          if (prediction_hit)
            prediction_hits <= prediction_hits + 1'b1;
          if (prediction_miss)
            prediction_misses <= prediction_misses + 1'b1;
          if (confidence_fallback)
            confidence_fallbacks <= confidence_fallbacks + 1'b1;
          if (fairness_fallback)
            fairness_fallbacks <= fairness_fallbacks + 1'b1;
        end
      end
    end else begin : metrics_disabled
      always_comb begin
        prediction_attempts = '0;
        prediction_hits = '0;
        prediction_misses = '0;
        confidence_fallbacks = '0;
        fairness_fallbacks = '0;
      end
    end
  endgenerate

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      output_valid <= 1'b0;
      output_event <= '0;
      output_source <= '0;
      fallback_start <= '0;
      history_valid <= 1'b0;
      history_source <= '0;
      predict_streak <= '0;
    end else begin
      if (slot_available) begin
        if (selected_source >= 0) begin
          if (bypass_hit) begin
            output_valid <= 1'b0;
          end else begin
            output_valid <= 1'b1;
            output_event <= source_event_local[selected_source];
            output_source <= SOURCE_WIDTH'(selected_source);
          end
          history_valid <= 1'b1;
          history_source <= SOURCE_WIDTH'(selected_source);
          if (selected_source == NUM_SOURCES-1)
            fallback_start <= '0;
          else
            fallback_start <= SOURCE_WIDTH'(selected_source + 1);
          if (prediction_hit)
            predict_streak <= predict_streak + 1'b1;
          else
            predict_streak <= '0;
        end else begin
          output_valid <= 1'b0;
          predict_streak <= '0;
        end
      end
    end
  end
endmodule
