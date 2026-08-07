`timescale 1ns/1ps

module a2_parameter_sweep_tb #(
  parameter int NUM_SOURCES = 16,
  parameter int BANK_COUNT = 2,
  parameter int RESERVOIR_DEPTH = 8,
  parameter int ENTER_LEVEL = RESERVOIR_DEPTH / 2,
  parameter int EXIT_LEVEL = 1,
  parameter int QUIET_CYCLES = 3,
  parameter int ADDR_WIDTH = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
);
  localparam int STIM_CYCLES = 256;
  localparam int MAX_PER_SOURCE = 1024;

  logic clk = 1'b0;
  logic rst_n;
  logic [NUM_SOURCES-1:0] source_valid;
  logic [NUM_SOURCES-1:0] source_ready;
  logic [ADDR_WIDTH-1:0] source_event [NUM_SOURCES];
  logic retire_valid;
  logic retire_ready;
  logic [ADDR_WIDTH-1:0] retire_event;
  logic [SOURCE_WIDTH-1:0] retire_source;

  logic [ADDR_WIDTH-1:0] expected_event [NUM_SOURCES][MAX_PER_SOURCE];
  integer expected_occurrence [NUM_SOURCES][MAX_PER_SOURCE];
  logic expected_sparse [NUM_SOURCES][MAX_PER_SOURCE];
  integer expected_head [NUM_SOURCES];
  integer expected_tail [NUM_SOURCES];
  integer pending_occurrence [NUM_SOURCES];
  logic pending_sparse [NUM_SOURCES];
  integer source_sequence [NUM_SOURCES];
  integer accepted_count;
  integer delivered_count;
  integer overrun_count;
  integer error_count;
  integer sparse_delivered;
  integer max_latency;
  integer clock_cycle;
  integer stim_cycle;
  integer source;
  integer group_index;
  integer selected_source;
  integer watchdog;
  integer latency;
  integer mode_transitions;
  logic previous_mode;

  always #5 clk = ~clk;

  a2_adaptive_dual_path_core #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RESERVOIR_DEPTH(RESERVOIR_DEPTH),
    .BANK_COUNT(BANK_COUNT),
    .ENTER_LEVEL(ENTER_LEVEL),
    .EXIT_LEVEL(EXIT_LEVEL),
    .QUIET_CYCLES(QUIET_CYCLES)
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

  task automatic fail(input string message);
    begin
      $error("A2_PARAM %s", message);
      error_count = error_count + 1;
    end
  endtask

  task automatic offer(input integer offered_source, input logic is_sparse);
    integer encoded_event;
    begin
      if (source_valid[offered_source]) begin
        overrun_count = overrun_count + 1;
      end else begin
        encoded_event = (offered_source << 10) | source_sequence[offered_source];
        source_valid[offered_source] = 1'b1;
        source_event[offered_source] = ADDR_WIDTH'(encoded_event);
        pending_occurrence[offered_source] = clock_cycle;
        pending_sparse[offered_source] = is_sparse;
        source_sequence[offered_source] = source_sequence[offered_source] + 1;
      end
    end
  endtask

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      clock_cycle = 0;
      accepted_count = 0;
      delivered_count = 0;
      sparse_delivered = 0;
      max_latency = 0;
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        expected_head[source] = 0;
        expected_tail[source] = 0;
      end
    end else begin
      clock_cycle = clock_cycle + 1;
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        if (source_valid[source] && source_ready[source]) begin
          expected_event[source][expected_tail[source]] = source_event[source];
          expected_occurrence[source][expected_tail[source]] =
            pending_occurrence[source];
          expected_sparse[source][expected_tail[source]] = pending_sparse[source];
          expected_tail[source] = expected_tail[source] + 1;
          accepted_count = accepted_count + 1;
          source_valid[source] <= 1'b0;
        end
      end
      if (retire_valid && retire_ready) begin
        if (retire_source >= NUM_SOURCES) begin
          fail("illegal retire source");
        end else if (expected_head[retire_source] >= expected_tail[retire_source]) begin
          fail("phantom or duplicate retirement");
        end else begin
          if (retire_event !== expected_event[retire_source][expected_head[retire_source]])
            fail("payload corruption or source-local reordering");
          latency = clock_cycle -
            expected_occurrence[retire_source][expected_head[retire_source]];
          if (latency > max_latency)
            max_latency = latency;
          if (expected_sparse[retire_source][expected_head[retire_source]]) begin
            sparse_delivered = sparse_delivered + 1;
            if (latency != 1)
              fail("isolated sparse event did not retire in one cycle");
          end
          expected_head[retire_source] = expected_head[retire_source] + 1;
          delivered_count = delivered_count + 1;
        end
      end
    end
  end

  always @(negedge clk or negedge rst_n) begin
    if (!rst_n) begin
      previous_mode = 1'b0;
      mode_transitions = 0;
    end else begin
      if (dut.burst_mode != previous_mode)
        mode_transitions = mode_transitions + 1;
      previous_mode = dut.burst_mode;
    end
  end

  initial begin
    rst_n = 1'b0;
    retire_ready = 1'b1;
    source_valid = '0;
    overrun_count = 0;
    error_count = 0;
    for (source = 0; source < NUM_SOURCES; source = source + 1) begin
      source_event[source] = '0;
      pending_occurrence[source] = 0;
      pending_sparse[source] = 1'b0;
      source_sequence[source] = 0;
    end
    repeat (4) @(posedge clk);
    @(negedge clk);
    rst_n = 1'b1;

    for (stim_cycle = 0; stim_cycle < STIM_CYCLES;
         stim_cycle = stim_cycle + 1) begin
      @(negedge clk);
      if ((stim_cycle < 32) || (stim_cycle >= 224)) begin
        if ((stim_cycle % 4) == 0)
          offer((stim_cycle/4) % NUM_SOURCES, stim_cycle < 32);
      end else if (stim_cycle < 96) begin
        if ((stim_cycle % 3) == 0) begin
          for (group_index = 0; group_index < BANK_COUNT + 2;
               group_index = group_index + 1) begin
            selected_source = group_index * BANK_COUNT;
            if (selected_source < NUM_SOURCES)
              offer(selected_source, 1'b0);
          end
        end
      end else if (stim_cycle < 160) begin
        for (group_index = 0; group_index < BANK_COUNT + 1;
             group_index = group_index + 1) begin
          selected_source = (stim_cycle + group_index*BANK_COUNT) % NUM_SOURCES;
          offer(selected_source, 1'b0);
        end
      end else if ((stim_cycle % 2) == 0) begin
        offer(stim_cycle % NUM_SOURCES, 1'b0);
      end else begin
        for (group_index = 0; group_index < BANK_COUNT + 1;
             group_index = group_index + 1)
          offer((stim_cycle + group_index) % NUM_SOURCES, 1'b0);
      end
    end

    @(negedge clk);
    watchdog = 0;
    while (((source_valid != '0) || (dut.reservoir_count != 0) || retire_valid) &&
           (watchdog < 4096)) begin
      @(negedge clk);
      watchdog = watchdog + 1;
    end
    if (watchdog >= 4096)
      fail("drain timeout");
    repeat (8) begin
      @(negedge clk);
      if (retire_valid)
        fail("late phantom after drain");
    end
    if (accepted_count != delivered_count)
      fail("accepted/delivered mismatch");
    if (sparse_delivered == 0)
      fail("no sparse probe was checked");
    if (error_count == 0) begin
      $display("A2_PARAMETER_PASS n=%0d banks=%0d depth=%0d enter=%0d exit=%0d dwell=%0d accepted=%0d delivered=%0d overrun=%0d sparse=%0d max_latency=%0d mode_transitions=%0d",
        NUM_SOURCES, BANK_COUNT, RESERVOIR_DEPTH, ENTER_LEVEL, EXIT_LEVEL,
        QUIET_CYCLES, accepted_count, delivered_count, overrun_count,
        sparse_delivered, max_latency, mode_transitions);
      $finish;
    end
    $fatal(1, "A2_PARAMETER_FAIL errors=%0d", error_count);
  end
endmodule
