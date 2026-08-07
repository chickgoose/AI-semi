`timescale 1ns/1ps

module a9_phase3_compare_tb;
  localparam int NUM_SOURCES = 16;
  localparam int ADDR_WIDTH = 16;
  localparam int RETIRE_LANES = 4;
  localparam int SOURCE_WIDTH = 4;
  localparam int STIM_CYCLES = 128;
  localparam int MAX_EVENTS = 512;

  logic clk = 1'b0;
  logic rst_n;
  logic [NUM_SOURCES-1:0] source_valid;
  logic [NUM_SOURCES-1:0] source_ready;
  logic [ADDR_WIDTH-1:0] source_event [NUM_SOURCES];
  logic [RETIRE_LANES-1:0] retire_valid;
  logic [RETIRE_LANES-1:0] retire_ready;
  logic [ADDR_WIDTH-1:0] retire_event [RETIRE_LANES];
  logic [SOURCE_WIDTH-1:0] retire_source [RETIRE_LANES];
  logic [ADDR_WIDTH-1:0] expected [NUM_SOURCES][MAX_EVENTS];
  integer expected_head [NUM_SOURCES];
  integer expected_tail [NUM_SOURCES];
  integer occurrence_cycle [NUM_SOURCES][MAX_EVENTS];
  integer generated_count;
  integer overrun_count;
  integer accepted_count;
  integer delivered_count;
  integer measurement_delivered;
  integer latency_sum;
  integer latency_max;
  integer cycle_count;
  integer workload;
  integer stimulus_cycle;
  integer source_index;
  integer lane_index;
  integer active_stripe;
  integer sequence_number [NUM_SOURCES];
  integer event_sequence;
  integer event_latency;
  integer timeout;
  logic stimulus_active;
  string implementation_name;
  string workload_name;

`ifdef A9_COMPARE_CENTRAL
  a9_centralized_reference #(
`elsif A9_COMPARE_DIFFUSIVE
  a9_neighbor_handoff_fabric #(
`else
  a9_distributed_token_fabric #(
`endif
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(RETIRE_LANES)
  ) dut (
    .clk_i(clk),
    .rst_ni(rst_n),
    .source_valid_i(source_valid),
    .source_ready_o(source_ready),
    .source_event_i(source_event),
    .retire_valid_o(retire_valid),
    .retire_ready_i(retire_ready),
    .retire_event_o(retire_event),
    .retire_source_o(retire_source)
  );

  always #5 clk = ~clk;

  task automatic occur(input integer source_id);
    integer next_sequence;
    begin
      generated_count = generated_count + 1;
      next_sequence = sequence_number[source_id];
      sequence_number[source_id] = sequence_number[source_id] + 1;
      occurrence_cycle[source_id][next_sequence] = cycle_count;
      if (source_valid[source_id]) begin
        overrun_count = overrun_count + 1;
      end else begin
        source_event[source_id] =
          ADDR_WIDTH'((source_id << 12) | next_sequence);
        source_valid[source_id] = 1'b1;
      end
    end
  endtask

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      accepted_count = 0;
      delivered_count = 0;
      measurement_delivered = 0;
      latency_sum = 0;
      latency_max = 0;
      cycle_count = 0;
      for (source_index = 0; source_index < NUM_SOURCES;
           source_index = source_index + 1) begin
        expected_head[source_index] = 0;
        expected_tail[source_index] = 0;
      end
    end else begin
      cycle_count = cycle_count + 1;
      for (source_index = 0; source_index < NUM_SOURCES;
           source_index = source_index + 1) begin
        if (source_valid[source_index] && source_ready[source_index]) begin
          expected[source_index][expected_tail[source_index]] =
            source_event[source_index];
          expected_tail[source_index] = expected_tail[source_index] + 1;
          accepted_count = accepted_count + 1;
          source_valid[source_index] <= 1'b0;
        end
      end
      for (lane_index = 0; lane_index < RETIRE_LANES;
           lane_index = lane_index + 1) begin
        if (retire_valid[lane_index] && retire_ready[lane_index]) begin
          source_index = retire_source[lane_index];
          if (expected_head[source_index] >= expected_tail[source_index])
            $fatal(1, "COMPARE phantom source=%0d", source_index);
          if (retire_event[lane_index] !==
              expected[source_index][expected_head[source_index]])
            $fatal(1, "COMPARE corrupt/reorder source=%0d", source_index);
          expected_head[source_index] = expected_head[source_index] + 1;
          delivered_count = delivered_count + 1;
          if (stimulus_active)
            measurement_delivered = measurement_delivered + 1;
          event_sequence = retire_event[lane_index] & 16'h0fff;
          event_latency = cycle_count -
                          occurrence_cycle[source_index][event_sequence];
          latency_sum = latency_sum + event_latency;
          if (event_latency > latency_max)
            latency_max = event_latency;
        end
      end
    end
  end

  initial begin
    if (!$value$plusargs("WORKLOAD=%d", workload))
      workload = 0;
`ifdef A9_COMPARE_CENTRAL
    implementation_name = "centralized";
`elsif A9_COMPARE_DIFFUSIVE
    implementation_name = "diffusive";
`else
    implementation_name = "static";
`endif
    case (workload)
      0: workload_name = "single_stripe_hotspot";
      1: workload_name = "moving_hotspot";
      2: workload_name = "alternating_stripe";
      3: workload_name = "all_stripe_saturation";
      default: $fatal(1, "COMPARE unknown workload=%0d", workload);
    endcase

    rst_n = 1'b0;
    source_valid = '0;
    retire_ready = '1;
    generated_count = 0;
    overrun_count = 0;
    stimulus_active = 1'b0;
    for (source_index = 0; source_index < NUM_SOURCES;
         source_index = source_index + 1) begin
      source_event[source_index] = '0;
      sequence_number[source_index] = 0;
    end
    repeat (3) @(negedge clk);
    rst_n = 1'b1;

    stimulus_active = 1'b1;
    for (stimulus_cycle = 0; stimulus_cycle < STIM_CYCLES;
         stimulus_cycle = stimulus_cycle + 1) begin
      @(negedge clk);
      case (workload)
        0: begin
          for (source_index = 0; source_index < 4; source_index++)
            occur(source_index);
        end
        1: begin
          active_stripe = (stimulus_cycle / 16) % RETIRE_LANES;
          for (source_index = active_stripe * 4;
               source_index < active_stripe * 4 + 4; source_index++)
            occur(source_index);
        end
        2: begin
          active_stripe = stimulus_cycle % 2;
          for (source_index = active_stripe * 4;
               source_index < active_stripe * 4 + 4; source_index++)
            occur(source_index);
        end
        3: begin
          for (source_index = 0; source_index < NUM_SOURCES; source_index++)
            occur(source_index);
        end
      endcase
    end
    stimulus_active = 1'b0;

    timeout = 0;
    while ((delivered_count != accepted_count) && timeout < 2000) begin
      @(negedge clk);
      timeout = timeout + 1;
    end
    if (delivered_count != accepted_count)
      $fatal(1, "COMPARE drain timeout accepted=%0d delivered=%0d",
             accepted_count, delivered_count);
    repeat (3) @(negedge clk);
    if (retire_valid != '0)
      $fatal(1, "COMPARE late duplicate valid=%b", retire_valid);

    $display("A9_PHASE3_RESULT implementation=%s workload=%s generated=%0d overrun=%0d accepted=%0d delivered=%0d measured_delivered=%0d stim_cycles=%0d throughput=%0.6f avg_latency=%0.6f max_latency=%0d",
      implementation_name, workload_name, generated_count, overrun_count,
      accepted_count, delivered_count, measurement_delivered, STIM_CYCLES,
      real'(measurement_delivered) / real'(STIM_CYCLES),
      (delivered_count == 0) ? 0.0 :
        real'(latency_sum) / real'(delivered_count), latency_max);
    $finish;
  end

  initial begin
    #500000;
    $fatal(1, "COMPARE global timeout");
  end
endmodule
