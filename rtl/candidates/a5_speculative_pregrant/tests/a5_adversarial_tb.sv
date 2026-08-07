`timescale 1ns/1ps

module a5_adversarial_tb;
  parameter int NUM_SOURCES = 16;
  parameter int ADDR_WIDTH = 16;
  parameter int SOURCE_WIDTH = 4;
  parameter bit ENABLE_PREDICTOR = 1'b1;
  parameter int PRED_HISTORY_BITS = 4;
  parameter int PRED_TABLE_ENTRIES = 16;
  parameter int PRED_CONF_WIDTH = 2;
  parameter int MAX_EVENTS = 4096;

  logic clk = 1'b0;
  logic rst_n;
  logic [NUM_SOURCES-1:0] source_valid;
  logic [ADDR_WIDTH-1:0] source_event [NUM_SOURCES];
  logic [NUM_SOURCES-1:0] source_ready;
  logic retire_valid;
  logic retire_ready;
  logic [ADDR_WIDTH-1:0] retire_event;
  logic [SOURCE_WIDTH-1:0] retire_source;
  logic [31:0] attempts;
  logic [31:0] hits;
  logic [31:0] misses;
  logic [31:0] confidence_fallbacks;
  logic [31:0] fairness_fallbacks;

  string pattern;
  integer requested_events;
  integer gap_cycles;
  integer dwell;
  integer affine;
  integer cycle_count;
  integer stimulus_cycle;
  integer generated;
  integer accepted;
  integer delivered;
  integer overrun;
  integer errors;
  integer measured_delivered;
  integer event_number;
  integer source_index;
  integer event_id;
  integer chosen_source;
  integer timeout;
  integer toggle_proxy;
  integer mispredict_same_cycle_recovery;
  integer e2e_sum;
  integer hit_latency_sum;
  integer hit_latency_count;
  integer fallback_latency_sum;
  integer fallback_latency_count;
  integer pending_id [NUM_SOURCES];
  integer record_source [MAX_EVENTS];
  integer record_occurrence [MAX_EVENTS];
  integer record_accept [MAX_EVENTS];
  integer record_delivery [MAX_EVENTS];
  integer record_predicted_hit [MAX_EVENTS];
  logic [ADDR_WIDTH-1:0] record_event [MAX_EVENTS];
  integer accepted_fifo [NUM_SOURCES][MAX_EVENTS];
  integer accepted_head [NUM_SOURCES];
  integer accepted_tail [NUM_SOURCES];
  logic [NUM_SOURCES-1:0] previous_source_ready;
  logic previous_retire_valid;
  logic [ADDR_WIDTH-1:0] previous_retire_event;
  logic [SOURCE_WIDTH-1:0] previous_retire_source;
  logic stimulus_active;

  always #5 clk = ~clk;

  a5_speculative_pregrant_core #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH),
    .ENABLE_PREDICTOR(ENABLE_PREDICTOR),
    .PRED_HISTORY_BITS(PRED_HISTORY_BITS),
    .PRED_TABLE_ENTRIES(PRED_TABLE_ENTRIES),
    .PRED_CONF_WIDTH(PRED_CONF_WIDTH),
    .MAX_PREDICT_STREAK(3)
  ) dut (
    .clk,
    .rst_n,
    .source_valid,
    .source_event,
    .source_ready,
    .retire_valid,
    .retire_ready,
    .retire_event,
    .retire_source,
    .prediction_attempts(attempts),
    .prediction_hits(hits),
    .prediction_misses(misses),
    .confidence_fallbacks,
    .fairness_fallbacks
  );

  function automatic integer pattern_source(input integer number);
    integer base_source;
    begin
      if (pattern == "alternating")
        base_source = number % 2;
      else if (pattern == "anticorrelated") begin
        case (number % 4)
          0: base_source = 0;
          1: base_source = 1;
          2: base_source = 0;
          default: base_source = 2;
        endcase
      end else if (pattern == "alias_collision")
        base_source = number % 4;
      else if (pattern == "cold_start")
        base_source = number % NUM_SOURCES;
      else
        base_source = (number / dwell) % 4;
      if (affine != 0)
        pattern_source = (5 * base_source + 3) % NUM_SOURCES;
      else
        pattern_source = base_source;
    end
  endfunction

  task automatic offer(input integer offered_source);
    logic [ADDR_WIDTH-1:0] value;
    begin
      if (generated >= MAX_EVENTS)
        $fatal(1, "A5 adversarial event capacity exceeded");
      event_id = generated;
      generated = generated + 1;
      value = ADDR_WIDTH'((offered_source << 8) | (event_id & 8'hff));
      record_source[event_id] = offered_source;
      record_occurrence[event_id] = cycle_count;
      record_accept[event_id] = -1;
      record_delivery[event_id] = -1;
      record_predicted_hit[event_id] = 0;
      record_event[event_id] = value;
      if (source_valid[offered_source]) begin
        overrun = overrun + 1;
      end else begin
        source_valid[offered_source] = 1'b1;
        source_event[offered_source] = value;
        pending_id[offered_source] = event_id;
      end
    end
  endtask

  always @(posedge clk or negedge rst_n) begin
    integer bit_index;
    integer latency;
    if (!rst_n) begin
      cycle_count = 0;
      accepted = 0;
      delivered = 0;
      measured_delivered = 0;
      errors = 0;
      toggle_proxy = 0;
      mispredict_same_cycle_recovery = 0;
      e2e_sum = 0;
      hit_latency_sum = 0;
      hit_latency_count = 0;
      fallback_latency_sum = 0;
      fallback_latency_count = 0;
      previous_source_ready = '0;
      previous_retire_valid = 1'b0;
      previous_retire_event = '0;
      previous_retire_source = '0;
      for (source_index = 0; source_index < NUM_SOURCES;
           source_index = source_index + 1) begin
        accepted_head[source_index] = 0;
        accepted_tail[source_index] = 0;
      end
    end else begin
      cycle_count = cycle_count + 1;
      for (bit_index = 0; bit_index < NUM_SOURCES; bit_index = bit_index + 1)
        toggle_proxy = toggle_proxy +
          (source_ready[bit_index] ^ previous_source_ready[bit_index]);
      toggle_proxy = toggle_proxy + (retire_valid ^ previous_retire_valid);
      for (bit_index = 0; bit_index < ADDR_WIDTH; bit_index = bit_index + 1)
        toggle_proxy = toggle_proxy +
          (retire_event[bit_index] ^ previous_retire_event[bit_index]);
      for (bit_index = 0; bit_index < SOURCE_WIDTH; bit_index = bit_index + 1)
        toggle_proxy = toggle_proxy +
          (retire_source[bit_index] ^ previous_retire_source[bit_index]);
      previous_source_ready = source_ready;
      previous_retire_valid = retire_valid;
      previous_retire_event = retire_event;
      previous_retire_source = retire_source;

      if (dut.prediction_miss) begin
        if (|(source_valid & source_ready))
          mispredict_same_cycle_recovery =
            mispredict_same_cycle_recovery + 1;
        else begin
          $error("A5 miss did not recover in the same cycle");
          errors = errors + 1;
        end
      end

      for (source_index = 0; source_index < NUM_SOURCES;
           source_index = source_index + 1) begin
        if (source_valid[source_index] && source_ready[source_index]) begin
          event_id = pending_id[source_index];
          record_accept[event_id] = cycle_count;
          record_predicted_hit[event_id] = dut.prediction_hit;
          accepted_fifo[source_index][accepted_tail[source_index]] = event_id;
          accepted_tail[source_index] = accepted_tail[source_index] + 1;
          accepted = accepted + 1;
          source_valid[source_index] <= 1'b0;
        end
      end

      if (retire_valid && retire_ready) begin
        chosen_source = int'(retire_source);
        if ((chosen_source < 0) || (chosen_source >= NUM_SOURCES) ||
            (accepted_head[chosen_source] >= accepted_tail[chosen_source])) begin
          $error("A5 adversarial phantom source=%0d", chosen_source);
          errors = errors + 1;
        end else begin
          event_id = accepted_fifo[chosen_source][accepted_head[chosen_source]];
          accepted_head[chosen_source] = accepted_head[chosen_source] + 1;
          if (retire_event !== record_event[event_id]) begin
            $error("A5 adversarial corruption id=%0d", event_id);
            errors = errors + 1;
          end
          record_delivery[event_id] = cycle_count;
          delivered = delivered + 1;
          if (stimulus_active)
            measured_delivered = measured_delivered + 1;
          latency = cycle_count - record_occurrence[event_id];
          e2e_sum = e2e_sum + latency;
          if (record_predicted_hit[event_id] != 0) begin
            hit_latency_sum = hit_latency_sum + latency;
            hit_latency_count = hit_latency_count + 1;
          end else begin
            fallback_latency_sum = fallback_latency_sum + latency;
            fallback_latency_count = fallback_latency_count + 1;
          end
        end
      end
    end
  end

  initial begin
    if (!$value$plusargs("PATTERN=%s", pattern))
      pattern = "alternating";
    if (!$value$plusargs("EVENTS=%d", requested_events))
      requested_events = 128;
    if (!$value$plusargs("GAP=%d", gap_cycles))
      gap_cycles = 2;
    if (!$value$plusargs("DWELL=%d", dwell))
      dwell = 4;
    if (!$value$plusargs("AFFINE=%d", affine))
      affine = 0;

    rst_n = 1'b0;
    retire_ready = 1'b1;
    source_valid = '0;
    generated = 0;
    overrun = 0;
    event_number = 0;
    stimulus_active = 1'b0;
    for (source_index = 0; source_index < NUM_SOURCES;
         source_index = source_index + 1) begin
      source_event[source_index] = '0;
      pending_id[source_index] = -1;
    end
    repeat (3) @(negedge clk);
    rst_n = 1'b1;
    stimulus_active = 1'b1;

    for (stimulus_cycle = 0;
         stimulus_cycle < requested_events * gap_cycles;
         stimulus_cycle = stimulus_cycle + 1) begin
      @(negedge clk);
      if (((stimulus_cycle % gap_cycles) == 0) &&
          (event_number < requested_events)) begin
        offer(pattern_source(event_number));
        event_number = event_number + 1;
      end
    end
    @(negedge clk);
    stimulus_active = 1'b0;

    timeout = 0;
    while (((source_valid != '0) || (accepted != delivered) || retire_valid) &&
           (timeout < 256)) begin
      @(negedge clk);
      timeout = timeout + 1;
    end
    if (timeout >= 256) begin
      $error("A5 adversarial drain timeout");
      errors = errors + 1;
    end
    if (accepted != delivered) begin
      $error("A5 adversarial conservation accepted=%0d delivered=%0d",
        accepted, delivered);
      errors = errors + 1;
    end
    if (attempts != hits + misses) begin
      $error("A5 predictor accounting attempts=%0d hits=%0d misses=%0d",
        attempts, hits, misses);
      errors = errors + 1;
    end
    if (mispredict_same_cycle_recovery != misses) begin
      $error("A5 recovery mismatch recovered=%0d misses=%0d",
        mispredict_same_cycle_recovery, misses);
      errors = errors + 1;
    end

    $display("A5_ADVERSARIAL_METRICS pattern=%s enabled=%0d history_bits=%0d table_entries=%0d conf_bits=%0d events=%0d generated=%0d overrun=%0d accepted=%0d delivered=%0d errors=%0d attempts=%0d hits=%0d misses=%0d confidence_fallbacks=%0d fairness_fallbacks=%0d same_cycle_recovery=%0d recovery_latency_cycles=0 throughput=%0.6f avg_e2e=%0.6f avg_hit_latency=%0.6f avg_fallback_latency=%0.6f toggles=%0d",
      pattern, ENABLE_PREDICTOR, PRED_HISTORY_BITS, PRED_TABLE_ENTRIES,
      PRED_CONF_WIDTH, requested_events, generated, overrun, accepted,
      delivered, errors, attempts, hits, misses, confidence_fallbacks,
      fairness_fallbacks, mispredict_same_cycle_recovery,
      real'(measured_delivered)/(requested_events*gap_cycles),
      delivered ? real'(e2e_sum)/delivered : 0.0,
      hit_latency_count ? real'(hit_latency_sum)/hit_latency_count : 0.0,
      fallback_latency_count ?
        real'(fallback_latency_sum)/fallback_latency_count : 0.0,
      toggle_proxy);
    if (errors != 0)
      $fatal(1, "A5_ADVERSARIAL_FAIL errors=%0d", errors);
    $display("A5_ADVERSARIAL_PASS");
    $finish;
  end
endmodule
