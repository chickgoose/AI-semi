`timescale 1ns/1ps

module a9_phase4_stall_sweep_tb;
  localparam int NUM_SOURCES = 16;
  localparam int ADDR_WIDTH = 16;
  localparam int RETIRE_LANES = 4;
  localparam int SOURCE_WIDTH = 4;
  localparam int STIM_CYCLES = 1000;
  localparam int MAX_EVENTS = 1200;

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
  integer generated_count;
  integer overrun_count;
  integer accepted_count;
  integer delivered_count;
  integer measured_delivered;
  integer migrated_count;
  integer stalled_head_cycles;
  integer pin_toggles;
  integer migration_signal_toggles;
  integer stall_pct;
  integer stimulus_cycle;
  integer source_index;
  integer lane_index;
  integer timeout;
  integer sequence_number [NUM_SOURCES];
  logic stimulus_active;
`ifdef A9_SWEEP_DIFFUSIVE
  logic [RETIRE_LANES-1:0] previous_pinned;
  logic [RETIRE_LANES-1:0] previous_migrate;
`endif
  string implementation_name;

`ifdef A9_SWEEP_CENTRAL
  a9_centralized_reference #(
`elsif A9_SWEEP_DIFFUSIVE
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

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      accepted_count = 0;
      delivered_count = 0;
      measured_delivered = 0;
      migrated_count = 0;
      stalled_head_cycles = 0;
      pin_toggles = 0;
      migration_signal_toggles = 0;
`ifdef A9_SWEEP_DIFFUSIVE
      previous_pinned = '0;
      previous_migrate = '0;
`endif
      for (source_index = 0; source_index < NUM_SOURCES;
           source_index = source_index + 1) begin
        expected_head[source_index] = 0;
        expected_tail[source_index] = 0;
      end
    end else begin
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
            $fatal(1, "STALL_SWEEP phantom source=%0d", source_index);
          if (retire_event[lane_index] !==
              expected[source_index][expected_head[source_index]])
            $fatal(1, "STALL_SWEEP corrupt/reorder source=%0d", source_index);
          expected_head[source_index] = expected_head[source_index] + 1;
          delivered_count = delivered_count + 1;
          if (stimulus_active) begin
            measured_delivered = measured_delivered + 1;
            if ((source_index < 4) && (lane_index == 1))
              migrated_count = migrated_count + 1;
          end
        end
      end
`ifdef A9_SWEEP_DIFFUSIVE
      if (stimulus_active && dut.base_valid[0] && !retire_ready[0])
        stalled_head_cycles = stalled_head_cycles + 1;
      for (lane_index = 0; lane_index < RETIRE_LANES;
           lane_index = lane_index + 1) begin
        if (dut.pinned_q[lane_index] != previous_pinned[lane_index])
          pin_toggles = pin_toggles + 1;
        if (dut.migrate[lane_index] != previous_migrate[lane_index])
          migration_signal_toggles = migration_signal_toggles + 1;
      end
      previous_pinned = dut.pinned_q;
      previous_migrate = dut.migrate;
`else
      if (stimulus_active && retire_valid[0] && !retire_ready[0])
        stalled_head_cycles = stalled_head_cycles + 1;
`endif
    end
  end

  initial begin
    if (!$value$plusargs("STALL_PCT=%d", stall_pct))
      stall_pct = 0;
    if ((stall_pct < 0) || (stall_pct > 100))
      $fatal(1, "STALL_SWEEP invalid percentage=%0d", stall_pct);
`ifdef A9_SWEEP_CENTRAL
    implementation_name = "centralized";
`elsif A9_SWEEP_DIFFUSIVE
    implementation_name = "diffusive";
`else
    implementation_name = "static";
`endif

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
      retire_ready = '1;
      retire_ready[0] = ((stimulus_cycle % 100) >= stall_pct);
      source_index = stimulus_cycle % 4;
      generated_count = generated_count + 1;
      if (source_valid[source_index]) begin
        overrun_count = overrun_count + 1;
        sequence_number[source_index] = sequence_number[source_index] + 1;
      end else begin
        source_event[source_index] = ADDR_WIDTH'(
          (source_index << 12) | sequence_number[source_index]);
        sequence_number[source_index] = sequence_number[source_index] + 1;
        source_valid[source_index] = 1'b1;
      end
    end
    stimulus_active = 1'b0;
    retire_ready = '1;
    timeout = 0;
    while ((accepted_count != delivered_count) && timeout < 3000) begin
      @(negedge clk);
      timeout = timeout + 1;
    end
    if (accepted_count != delivered_count)
      $fatal(1, "STALL_SWEEP drain timeout accepted=%0d delivered=%0d",
             accepted_count, delivered_count);

    $display("A9_PHASE4_STALL_RESULT implementation=%s stall_pct=%0d generated=%0d overrun=%0d accepted=%0d delivered=%0d measured_delivered=%0d migrations=%0d stalled_head_cycles=%0d migration_coverage=%0.6f pin_toggles=%0d migration_signal_toggles=%0d added_toggles_per_event=%0.6f",
      implementation_name, stall_pct, generated_count, overrun_count,
      accepted_count, delivered_count, measured_delivered, migrated_count,
      stalled_head_cycles,
      (stalled_head_cycles == 0) ? 0.0 :
        real'(migrated_count) / real'(stalled_head_cycles),
      pin_toggles, migration_signal_toggles,
      (measured_delivered == 0) ? 0.0 :
        real'(pin_toggles + migration_signal_toggles) /
        real'(measured_delivered));
    $finish;
  end

  initial begin
    #1000000;
    $fatal(1, "STALL_SWEEP global timeout");
  end
endmodule
