`timescale 1ns/1ps

module aer_clean_tb;
  parameter int NUM_SOURCES = 4;
  parameter int ADDR_WIDTH = 16;
  parameter int RETIRE_LANES = 2;
  parameter int FIFO_DEPTH = 4;
  parameter int DEFAULT_STIM_CYCLES = 256;
  parameter int TIMEOUT_CYCLES = 20000;
  parameter int MAX_EVENTS = 131072;

  logic clk = 1'b0;
  always #5 clk = ~clk;

  aer_bench_if #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(RETIRE_LANES)
  ) bench(clk);

  aer_legacy_candidate_adapter #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(RETIRE_LANES),
    .FIFO_DEPTH(FIFO_DEPTH)
  ) candidate(bench);

  aer_clean_assertions #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(RETIRE_LANES)
  ) assertions(bench);

  string test_name;
  string metrics_path;
  integer metrics_fd;
  integer stim_cycles;
  integer load_pct;
  integer seed;
  integer burst_period;

  logic [NUM_SOURCES-1:0] pending;
  logic [ADDR_WIDTH-1:0] pending_event [NUM_SOURCES];
  integer pending_id [NUM_SOURCES];
  integer source_sequence [NUM_SOURCES];
  integer request_wait [NUM_SOURCES];

  integer record_source [MAX_EVENTS];
  integer record_sequence [MAX_EVENTS];
  integer record_occurrence [MAX_EVENTS];
  integer record_accept [MAX_EVENTS];
  integer record_delivery [MAX_EVENTS];
  logic [ADDR_WIDTH-1:0] record_event [MAX_EVENTS];
  integer record_state [MAX_EVENTS]; // 0=offered, 1=overrun, 2=accepted, 3=delivered

  integer accepted_fifo [NUM_SOURCES][MAX_EVENTS];
  integer accepted_head [NUM_SOURCES];
  integer accepted_tail [NUM_SOURCES];
  integer delivered_by_source [NUM_SOURCES];
  integer last_delivered_occurrence [NUM_SOURCES];
  integer last_delivered_cycle [NUM_SOURCES];

  integer generated_count;
  integer source_overrun_count;
  integer accepted_count;
  integer delivered_count;
  integer error_count;
  integer cycle_count;
  integer first_occurrence_cycle;
  integer last_delivery_cycle;
  integer e2e_latency_sum;
  integer internal_latency_sum;
  integer max_e2e_latency;
  integer max_internal_latency;
  integer max_request_wait;
  integer timing_error_sum;
  integer timing_interval_count;
  integer max_timing_error;
  integer rng_state;
  integer drive_source;
  integer monitor_source;
  integer monitor_lane;
  integer init_source;
  integer event_id;
  integer decoded_source;
  integer e2e_latency;
  integer internal_latency;
  integer interval_error;
  integer timeout;
  integer stim_cycle;
  logic [ADDR_WIDTH-1:0] expected_event;

  always_comb begin
    bench.source_valid = pending;
    for (drive_source = 0; drive_source < NUM_SOURCES;
         drive_source = drive_source + 1)
      bench.source_event[drive_source] = pending_event[drive_source];
  end

  function automatic logic [ADDR_WIDTH-1:0] make_event(
    input integer source_index,
    input integer event_sequence
  );
    // Address is the source coordinate plus a legitimate one-bit event type.
    // The event sequence remains testbench-only.
    make_event = ADDR_WIDTH'((source_index << 1) | (event_sequence & 1));
  endfunction

  function automatic integer abs_integer(input integer value);
    if (value < 0)
      abs_integer = -value;
    else
      abs_integer = value;
  endfunction

  function automatic integer outstanding_count();
    integer i;
    begin
      outstanding_count = 0;
      for (i = 0; i < NUM_SOURCES; i = i + 1)
        outstanding_count = outstanding_count + accepted_tail[i] - accepted_head[i];
    end
  endfunction

  function automatic real average_e2e_latency();
    if (delivered_count == 0)
      average_e2e_latency = 0.0;
    else
      average_e2e_latency = real'(e2e_latency_sum) / delivered_count;
  endfunction

  function automatic real average_internal_latency();
    if (delivered_count == 0)
      average_internal_latency = 0.0;
    else
      average_internal_latency = real'(internal_latency_sum) / delivered_count;
  endfunction

  function automatic real average_timing_error();
    if (timing_interval_count == 0)
      average_timing_error = 0.0;
    else
      average_timing_error = real'(timing_error_sum) / timing_interval_count;
  endfunction

  function automatic real throughput();
    integer span;
    begin
      span = last_delivery_cycle - first_occurrence_cycle + 1;
      if ((delivered_count == 0) || (span <= 0))
        throughput = 0.0;
      else
        throughput = real'(delivered_count) / span;
    end
  endfunction

  function automatic real fairness_index();
    integer i;
    real sum;
    real square_sum;
    begin
      sum = 0.0;
      square_sum = 0.0;
      for (i = 0; i < NUM_SOURCES; i = i + 1) begin
        sum = sum + delivered_by_source[i];
        square_sum = square_sum + delivered_by_source[i] * delivered_by_source[i];
      end
      if (square_sum == 0.0)
        fairness_index = 1.0;
      else
        fairness_index = (sum * sum) / (NUM_SOURCES * square_sum);
    end
  endfunction

  task automatic offer_event(input integer source_index);
    integer new_event_id;
    begin
      if (generated_count >= MAX_EVENTS)
        $fatal(1, "CLEAN_BENCH record capacity exceeded");

      new_event_id = generated_count;
      generated_count = generated_count + 1;
      record_source[new_event_id] = source_index;
      record_sequence[new_event_id] = source_sequence[source_index];
      record_occurrence[new_event_id] = cycle_count;
      record_accept[new_event_id] = -1;
      record_delivery[new_event_id] = -1;
      record_event[new_event_id] =
        make_event(source_index, source_sequence[source_index]);
      record_state[new_event_id] = 0;
      source_sequence[source_index] = source_sequence[source_index] + 1;

      if (first_occurrence_cycle < 0)
        first_occurrence_cycle = cycle_count;

      if (pending[source_index]) begin
        record_state[new_event_id] = 1;
        source_overrun_count = source_overrun_count + 1;
      end else begin
        pending[source_index] = 1'b1;
        pending_event[source_index] = record_event[new_event_id];
        pending_id[source_index] = new_event_id;
      end
    end
  endtask

  task automatic seeded_offer(input integer source_index, input integer percentage);
    integer draw;
    begin
      rng_state = (rng_state * 1103515245) + 12345 + source_index;
      draw = (rng_state & 32'h7fffffff) % 100;
      if (draw < percentage)
        offer_event(source_index);
    end
  endtask

  task automatic generate_workload(input integer local_cycle);
    integer cluster_size;
    integer stimulus_source;
    begin
      if (test_name == "basic_single") begin
        if ((local_cycle < 64) && ((local_cycle % 4) == 0) && !pending[0])
          offer_event(0);
      end else if (test_name == "basic_sparse") begin
        if ((local_cycle % 8) == 0)
          offer_event((local_cycle/8 + seed) % NUM_SOURCES);
      end else if (test_name == "basic_simultaneous") begin
        if ((local_cycle == 4) || (local_cycle == 36))
          for (stimulus_source = 0; stimulus_source < NUM_SOURCES;
               stimulus_source = stimulus_source + 1)
            offer_event(stimulus_source);
      end else if (test_name == "basic_backpressure") begin
        if ((local_cycle % 10) == 0)
          offer_event((local_cycle/10 + seed) % NUM_SOURCES);
      end else if (test_name == "limit_load") begin
        for (stimulus_source = 0; stimulus_source < NUM_SOURCES;
             stimulus_source = stimulus_source + 1)
          seeded_offer(stimulus_source, load_pct);
      end else if (test_name == "limit_elephant_mouse") begin
        offer_event(0);
        if ((local_cycle % 16) == 0)
          offer_event(NUM_SOURCES-1);
      end else if (test_name == "limit_global_fanin") begin
        if ((local_cycle % burst_period) == 0)
          for (stimulus_source = 0; stimulus_source < NUM_SOURCES;
               stimulus_source = stimulus_source + 1)
            offer_event(stimulus_source);
      end else if (test_name == "limit_local_cluster") begin
        cluster_size = (NUM_SOURCES < 4) ? NUM_SOURCES : 4;
        if ((local_cycle % burst_period) == 0)
          for (stimulus_source = 0; stimulus_source < cluster_size;
               stimulus_source = stimulus_source + 1)
            offer_event(stimulus_source);
      end else if (test_name == "limit_distributed_burst") begin
        if ((local_cycle % burst_period) == 0) begin
          offer_event(0);
          if (NUM_SOURCES > 1)
            offer_event(NUM_SOURCES/3);
          if (NUM_SOURCES > 2)
            offer_event((2*NUM_SOURCES)/3);
          if (NUM_SOURCES > 3)
            offer_event(NUM_SOURCES-1);
        end
      end else if (test_name == "limit_retrigger") begin
        offer_event(0);
      end else if (test_name == "limit_timing_fidelity") begin
        if (((local_cycle % 16) == 0) || ((local_cycle % 16) == 2))
          offer_event(0);
        for (stimulus_source = 1; stimulus_source < NUM_SOURCES;
             stimulus_source = stimulus_source + 1)
          seeded_offer(stimulus_source, load_pct);
      end else if (test_name == "limit_backpressure_shock") begin
        for (stimulus_source = 0; stimulus_source < NUM_SOURCES;
             stimulus_source = stimulus_source + 1)
          seeded_offer(stimulus_source, load_pct);
      end else begin
        $fatal(2, "Unknown CLEAN_TEST=%s", test_name);
      end
    end
  endtask

  task automatic drive_sink_ready(input integer local_cycle);
    begin
      bench.retire_ready = '1;
      if (test_name == "basic_backpressure") begin
        if ((local_cycle % 5) >= 2)
          bench.retire_ready = '0;
      end else if (test_name == "limit_backpressure_shock") begin
        if ((local_cycle >= stim_cycles/4) && (local_cycle < stim_cycles/2))
          bench.retire_ready = '0;
      end
    end
  endtask

  always @(posedge clk or negedge bench.rst_n) begin
    if (!bench.rst_n) begin
      cycle_count = 0;
      accepted_count = 0;
      delivered_count = 0;
      error_count = 0;
      e2e_latency_sum = 0;
      internal_latency_sum = 0;
      max_e2e_latency = 0;
      max_internal_latency = 0;
      max_request_wait = 0;
      timing_error_sum = 0;
      timing_interval_count = 0;
      max_timing_error = 0;
      last_delivery_cycle = -1;
      for (monitor_source = 0; monitor_source < NUM_SOURCES;
           monitor_source = monitor_source + 1) begin
        accepted_head[monitor_source] = 0;
        accepted_tail[monitor_source] = 0;
        delivered_by_source[monitor_source] = 0;
        request_wait[monitor_source] = 0;
        last_delivered_occurrence[monitor_source] = -1;
        last_delivered_cycle[monitor_source] = -1;
      end
    end else begin
      cycle_count = cycle_count + 1;

      for (monitor_source = 0; monitor_source < NUM_SOURCES;
           monitor_source = monitor_source + 1) begin
        if (bench.source_valid[monitor_source] &&
            !bench.source_ready[monitor_source]) begin
          request_wait[monitor_source] = request_wait[monitor_source] + 1;
          if (request_wait[monitor_source] > max_request_wait)
            max_request_wait = request_wait[monitor_source];
        end else begin
          request_wait[monitor_source] = 0;
        end

        if (bench.source_valid[monitor_source] &&
            bench.source_ready[monitor_source]) begin
          event_id = pending_id[monitor_source];
          record_accept[event_id] = cycle_count;
          record_state[event_id] = 2;
          accepted_fifo[monitor_source][accepted_tail[monitor_source]] = event_id;
          accepted_tail[monitor_source] = accepted_tail[monitor_source] + 1;
          accepted_count = accepted_count + 1;
          pending[monitor_source] <= 1'b0;
        end
      end

      for (monitor_lane = 0; monitor_lane < RETIRE_LANES;
           monitor_lane = monitor_lane + 1) begin
        if (bench.retire_valid[monitor_lane] &&
            bench.retire_ready[monitor_lane]) begin
          decoded_source = integer'(bench.retire_event[monitor_lane] >> 1);
          if ($isunknown(bench.retire_event[monitor_lane])) begin
            $error("CLEAN_SCOREBOARD unknown completed event lane=%0d",
                   monitor_lane);
            error_count = error_count + 1;
          end else if ((decoded_source < 0) || (decoded_source >= NUM_SOURCES)) begin
            $error("CLEAN_SCOREBOARD illegal event address=0x%0h",
                   bench.retire_event[monitor_lane]);
            error_count = error_count + 1;
          end else if (accepted_head[decoded_source] >= accepted_tail[decoded_source]) begin
            $error("CLEAN_SCOREBOARD phantom/duplicate event source=%0d event=0x%0h",
                   decoded_source, bench.retire_event[monitor_lane]);
            error_count = error_count + 1;
          end else begin
            event_id = accepted_fifo[decoded_source][accepted_head[decoded_source]];
            expected_event = record_event[event_id];
            if (bench.retire_event[monitor_lane] !== expected_event) begin
              $error("CLEAN_SCOREBOARD event mismatch id=%0d expected=0x%0h actual=0x%0h",
                     event_id, expected_event, bench.retire_event[monitor_lane]);
              error_count = error_count + 1;
            end

            record_delivery[event_id] = cycle_count;
            record_state[event_id] = 3;
            accepted_head[decoded_source] = accepted_head[decoded_source] + 1;
            delivered_by_source[decoded_source] =
              delivered_by_source[decoded_source] + 1;
            delivered_count = delivered_count + 1;
            last_delivery_cycle = cycle_count;

            e2e_latency = cycle_count - record_occurrence[event_id];
            internal_latency = cycle_count - record_accept[event_id];
            e2e_latency_sum = e2e_latency_sum + e2e_latency;
            internal_latency_sum = internal_latency_sum + internal_latency;
            if (e2e_latency > max_e2e_latency)
              max_e2e_latency = e2e_latency;
            if (internal_latency > max_internal_latency)
              max_internal_latency = internal_latency;

            if (last_delivered_occurrence[decoded_source] >= 0) begin
              interval_error = abs_integer(
                (cycle_count - last_delivered_cycle[decoded_source]) -
                (record_occurrence[event_id] -
                 last_delivered_occurrence[decoded_source]));
              timing_error_sum = timing_error_sum + interval_error;
              timing_interval_count = timing_interval_count + 1;
              if (interval_error > max_timing_error)
                max_timing_error = interval_error;
            end
            last_delivered_occurrence[decoded_source] = record_occurrence[event_id];
            last_delivered_cycle[decoded_source] = cycle_count;
          end
        end
      end
    end
  end

  task automatic write_metrics();
    begin
      metrics_fd = $fopen(metrics_path, "w");
      if (metrics_fd == 0) begin
        $error("Cannot open clean benchmark metrics path: %s", metrics_path);
        error_count = error_count + 1;
      end else begin
        $fdisplay(metrics_fd,
          "test,seed,load_pct,stim_cycles,generated,source_overrun,accepted,delivered,errors,total_cycles,avg_e2e_latency,max_e2e_latency,avg_internal_latency,max_internal_latency,throughput,fairness,max_request_wait,avg_timing_error,max_timing_error");
        $fdisplay(metrics_fd,
          "%s,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0.6f,%0d,%0.6f,%0d,%0.6f,%0.6f,%0d,%0.6f,%0d",
          test_name, seed, load_pct, stim_cycles, generated_count,
          source_overrun_count, accepted_count, delivered_count, error_count,
          cycle_count, average_e2e_latency(), max_e2e_latency,
          average_internal_latency(), max_internal_latency, throughput(),
          fairness_index(), max_request_wait, average_timing_error(),
          max_timing_error);
        $fclose(metrics_fd);
      end

      $display("AER_CLEAN_METRICS test=%s seed=%0d load_pct=%0d generated=%0d overrun=%0d accepted=%0d delivered=%0d errors=%0d throughput=%0.6f avg_e2e=%0.4f max_e2e=%0d fairness=%0.6f max_wait=%0d avg_timing_error=%0.4f",
        test_name, seed, load_pct, generated_count, source_overrun_count,
        accepted_count, delivered_count, error_count, throughput(),
        average_e2e_latency(), max_e2e_latency, fairness_index(),
        max_request_wait, average_timing_error());
    end
  endtask

  initial begin
    if (!$value$plusargs("CLEAN_TEST=%s", test_name))
      test_name = "basic_single";
    if (!$value$plusargs("METRICS=%s", metrics_path))
      metrics_path = "aer_clean_metrics.csv";
    if (!$value$plusargs("STIM_CYCLES=%d", stim_cycles))
      stim_cycles = DEFAULT_STIM_CYCLES;
    if (!$value$plusargs("LOAD_PCT=%d", load_pct))
      load_pct = 3;
    if (!$value$plusargs("SEED=%d", seed))
      seed = 1;
    if (!$value$plusargs("BURST_PERIOD=%d", burst_period))
      burst_period = 16;

    generated_count = 0;
    source_overrun_count = 0;
    first_occurrence_cycle = -1;
    rng_state = seed;
    pending = '0;
    bench.rst_n = 1'b0;
    bench.retire_ready = '0;
    for (init_source = 0; init_source < NUM_SOURCES;
         init_source = init_source + 1) begin
      pending_event[init_source] = '0;
      pending_id[init_source] = -1;
      source_sequence[init_source] = 0;
    end

    repeat (4) @(posedge clk);
    @(negedge clk);
    bench.rst_n = 1'b1;

    for (stim_cycle = 0; stim_cycle < stim_cycles; stim_cycle = stim_cycle + 1) begin
      @(negedge clk);
      drive_sink_ready(stim_cycle);
      generate_workload(stim_cycle);
    end

    @(negedge clk);
    bench.retire_ready = '1;
    timeout = 0;
    while (((pending != '0) || (outstanding_count() != 0) ||
            (bench.retire_valid != '0)) && (timeout < TIMEOUT_CYCLES)) begin
      @(negedge clk);
      timeout = timeout + 1;
    end

    if (timeout >= TIMEOUT_CYCLES) begin
      $error("CLEAN_BENCH drain timeout pending=%0h outstanding=%0d",
             pending, outstanding_count());
      error_count = error_count + 1;
    end
    if (accepted_count != delivered_count) begin
      $error("CLEAN_SCOREBOARD missing accepted events accepted=%0d delivered=%0d",
             accepted_count, delivered_count);
      error_count = error_count + 1;
    end

    write_metrics();
    if (error_count == 0)
      $display("AER_CLEAN_TEST_PASS %s", test_name);
    else
      $fatal(1, "AER_CLEAN_TEST_FAIL %s errors=%0d", test_name, error_count);
    $finish;
  end
endmodule
