`timescale 1ns/1ps

module ganghee_a23_compat_tb;
  parameter int NUM_SOURCES = 16;
  parameter int ADDR_WIDTH = 32;
  parameter int QDEPTH = 1024;
  parameter int SCOREBOARD_DEPTH = 262144;
  localparam int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES);
  localparam int GRID_SIDE = (NUM_SOURCES == 64) ? 8 : 4;
  localparam int SEQUENCE_WIDTH = ADDR_WIDTH - 8;

  logic clk = 1'b0;
  logic rst_n = 1'b0;
  always #5 clk = ~clk;

  logic [NUM_SOURCES-1:0] in_valid;
  logic [NUM_SOURCES-1:0] in_ready;
  logic [NUM_SOURCES-1:0][ADDR_WIDTH-1:0] in_addr;
  logic [NUM_SOURCES-1:0] accepted_pulse;
  logic out_valid;
  logic out_ready;
  logic [ADDR_WIDTH-1:0] out_addr;
  logic [SOURCE_WIDTH-1:0] out_source;

  logic [ADDR_WIDTH-1:0] source_queue [0:NUM_SOURCES-1][0:QDEPTH-1];
  integer source_arrival_cycle [0:NUM_SOURCES-1][0:QDEPTH-1];
  integer queue_head [0:NUM_SOURCES-1];
  integer queue_tail [0:NUM_SOURCES-1];
  integer queue_count [0:NUM_SOURCES-1];
  logic pending_valid [0:NUM_SOURCES-1];
  logic [ADDR_WIDTH-1:0] pending_payload [0:NUM_SOURCES-1];
  integer pending_arrival_cycle [0:NUM_SOURCES-1];
  integer sequence_number [0:NUM_SOURCES-1];

  logic [SOURCE_WIDTH-1:0] expected_source [0:SCOREBOARD_DEPTH-1];
  logic [ADDR_WIDTH-1:0] expected_payload [0:SCOREBOARD_DEPTH-1];
  integer expected_accept_cycle [0:SCOREBOARD_DEPTH-1];
  integer expected_arrival_cycle [0:SCOREBOARD_DEPTH-1];
  integer expected_head;
  integer expected_tail;

  integer generated_count;
  integer accepted_count;
  integer emitted_count;
  integer overflow_count;
  integer phantom_count;
  integer duplicate_count;
  integer corruption_count;
  integer reorder_count;
  integer failures;
  integer workload_cycle;
  integer wall_cycle;
  integer first_accept_cycle;
  integer last_emit_cycle;
  integer fabric_latency_sum;
  integer fabric_latency_max;
  integer end_to_end_latency_sum;
  integer end_to_end_latency_max;
  integer accepted_by_source [0:NUM_SOURCES-1];
  integer emitted_by_source [0:NUM_SOURCES-1];
  integer service_last_ordinal [0:NUM_SOURCES-1];
  logic service_seen [0:NUM_SOURCES-1];
  integer service_ordinal;
  integer max_service_gap;
  integer last_emitted_sequence [0:NUM_SOURCES-1];

  logic observation_window_open;
  integer window_emitted_count;
  integer window_overflow_count;
  integer window_latency_sum;
  integer window_latency_max;
  integer window_emitted_by_source [0:NUM_SOURCES-1];

  logic previous_output_stall;
  logic [ADDR_WIDTH-1:0] stalled_output_addr;
  logic [SOURCE_WIDTH-1:0] stalled_output_source;
  logic previous_input_stall [0:NUM_SOURCES-1];
  logic [ADDR_WIDTH-1:0] stalled_input_addr [0:NUM_SOURCES-1];
  logic [SOURCE_WIDTH-1:0] expected_priority;

  integer seed;
  integer cycles;
  integer arrival_pct;
  integer background_pct;
  integer hotspot_pct;
  integer phase_len;
  integer long_stall_start;
  integer long_stall_cycles;
  integer rng_seed;
  logic [31:0] backpressure_rng_state;
  string workload;
  string hotspot_set;
  string backpressure;

  a23_ee430_core #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_INDEX_WIDTH(SOURCE_WIDTH)
  ) dut (
    .clk_i(clk),
    .rst_ni(rst_n),
    .src_valid_i(in_valid),
    .src_ready_o(in_ready),
    .src_addr_i(in_addr),
    .event_valid_o(out_valid),
    .event_ready_i(out_ready),
    .event_addr_o(out_addr),
    .event_source_o(out_source)
  );

  function automatic logic [31:0] next_random(input logic [31:0] current);
    next_random = current * 32'd1664525 + 32'd1013904223;
  endfunction

  function automatic integer clamp_pct(input integer value);
    if (value < 0)
      clamp_pct = 0;
    else if (value > 100)
      clamp_pct = 100;
    else
      clamp_pct = value;
  endfunction

  function automatic integer source_row(input integer source);
    source_row = source / GRID_SIDE;
  endfunction

  function automatic integer source_col(input integer source);
    source_col = source % GRID_SIDE;
  endfunction

  function automatic integer is_fixed_hotspot(input integer source);
    integer row;
    integer col;
    begin
      row = source_row(source);
      col = source_col(source);
      if (NUM_SOURCES == 16) begin
        if (hotspot_set == "center")
          is_fixed_hotspot = ((source == 5) || (source == 6) ||
                              (source == 9) || (source == 10));
        else
          is_fixed_hotspot = ((source == 0) || (source == 3) ||
                              (source == 12) || (source == 15));
      end else begin
        // The audited fixed 8x8 workload uses whole-row regions: center is
        // rows 2..5 and periphery is rows 0,1,6,7 (32 sources in each set).
        if (hotspot_set == "center")
          is_fixed_hotspot = ((row >= 2) && (row <= 5));
        else
          is_fixed_hotspot = ((row == 0) || (row == 1) ||
                              (row == 6) || (row == 7));
      end
    end
  endfunction

  function automatic integer is_moving_hotspot(
    input integer source,
    input integer cycle_index
  );
    integer phase;
    integer row;
    begin
      phase = (cycle_index / phase_len) % 2;
      row = source_row(source);
      if (NUM_SOURCES == 16) begin
        if (phase == 0)
          is_moving_hotspot = ((source == 5) || (source == 6) ||
                               (source == 9) || (source == 10));
        else
          is_moving_hotspot = ((source == 0) || (source == 3) ||
                               (source == 12) || (source == 15));
      end else begin
        if (phase == 0)
          is_moving_hotspot = ((row >= 2) && (row <= 5));
        else
          is_moving_hotspot = ((row == 0) || (row == 1) ||
                               (row == 6) || (row == 7));
      end
    end
  endfunction

  function automatic integer source_arrival_pct(
    input integer source,
    input integer cycle_index
  );
    begin
      if (workload == "uniform")
        source_arrival_pct = clamp_pct(arrival_pct);
      else if (workload == "hotspot")
        source_arrival_pct = clamp_pct(
          is_fixed_hotspot(source) ? hotspot_pct : background_pct);
      else if (workload == "moving-hotspot")
        source_arrival_pct = clamp_pct(
          is_moving_hotspot(source, cycle_index) ? hotspot_pct : background_pct);
      else
        source_arrival_pct = 0;
    end
  endfunction

  function automatic logic [ADDR_WIDTH-1:0] make_payload(
    input integer source,
    input integer sequence_index
  );
    logic [ADDR_WIDTH-1:0] value;
    begin
      value = '0;
      value[ADDR_WIDTH-1 -: 8] = 8'(source);
      value[SEQUENCE_WIDTH-1:0] = SEQUENCE_WIDTH'(sequence_index);
      make_payload = value;
    end
  endfunction

  function automatic integer one_count(input logic [NUM_SOURCES-1:0] value);
    integer source;
    begin
      one_count = 0;
      for (source = 0; source < NUM_SOURCES; source = source + 1)
        one_count = one_count + value[source];
    end
  endfunction

  task automatic record_failure(input string reason);
    begin
      failures = failures + 1;
      $display("GANGHEE_A23_FAIL seed=%0d N=%0d workload=%s cycle=%0d reason=%s",
        seed, NUM_SOURCES, workload, wall_cycle, reason);
    end
  endtask

  task automatic clear_state;
    integer source;
    begin
      in_valid = '0;
      in_addr = '0;
      out_ready = 1'b0;
      generated_count = 0;
      accepted_count = 0;
      emitted_count = 0;
      overflow_count = 0;
      phantom_count = 0;
      duplicate_count = 0;
      corruption_count = 0;
      reorder_count = 0;
      failures = 0;
      workload_cycle = 0;
      wall_cycle = 0;
      expected_head = 0;
      expected_tail = 0;
      first_accept_cycle = -1;
      last_emit_cycle = -1;
      fabric_latency_sum = 0;
      fabric_latency_max = 0;
      end_to_end_latency_sum = 0;
      end_to_end_latency_max = 0;
      service_ordinal = 0;
      max_service_gap = 0;
      observation_window_open = 1'b1;
      window_emitted_count = 0;
      window_overflow_count = 0;
      window_latency_sum = 0;
      window_latency_max = 0;
      previous_output_stall = 1'b0;
      stalled_output_addr = '0;
      stalled_output_source = '0;
      expected_priority = '0;
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        queue_head[source] = 0;
        queue_tail[source] = 0;
        queue_count[source] = 0;
        pending_valid[source] = 1'b0;
        pending_payload[source] = '0;
        pending_arrival_cycle[source] = 0;
        sequence_number[source] = 0;
        accepted_by_source[source] = 0;
        emitted_by_source[source] = 0;
        service_last_ordinal[source] = 0;
        service_seen[source] = 1'b0;
        last_emitted_sequence[source] = -1;
        window_emitted_by_source[source] = 0;
        previous_input_stall[source] = 1'b0;
        stalled_input_addr[source] = '0;
      end
    end
  endtask

  task automatic drive_backpressure;
    integer random_value;
    begin
      if (backpressure == "always") begin
        out_ready = 1'b1;
      end else if (backpressure == "alternating") begin
        out_ready = wall_cycle[0];
      end else if (backpressure == "random") begin
        backpressure_rng_state = next_random(backpressure_rng_state);
        random_value = backpressure_rng_state;
        out_ready = random_value[0] | random_value[3];
      end else if (backpressure == "long-stall") begin
        out_ready = !((wall_cycle >= long_stall_start) &&
                      (wall_cycle < long_stall_start + long_stall_cycles));
      end else begin
        out_ready = 1'b1;
      end
    end
  endtask

  // Workload generation and the ready/valid adapter run on the falling edge.
  // A queued entry is removed from the workload queue only when it becomes the
  // single pending event. The pending event remains stable until A23 accepts it.
  always @(negedge clk) begin : workload_driver
    integer source;
    integer threshold;
    integer slot;
    logic [ADDR_WIDTH-1:0] new_payload;
    if (rst_n) begin
      wall_cycle = wall_cycle + 1;
      drive_backpressure();

      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        if (accepted_pulse[source])
          pending_valid[source] = 1'b0;
      end

      if (workload_cycle < cycles) begin
        for (source = 0; source < NUM_SOURCES; source = source + 1) begin
          threshold = source_arrival_pct(source, workload_cycle);
          if (((($random(rng_seed) % 100 + 100) % 100) < threshold)) begin
            new_payload = make_payload(source, sequence_number[source]);
            sequence_number[source] = sequence_number[source] + 1;
            generated_count = generated_count + 1;
            if ((queue_count[source] + pending_valid[source] +
                 accepted_by_source[source] - emitted_by_source[source]) >= QDEPTH) begin
              overflow_count = overflow_count + 1;
            end else begin
              slot = queue_tail[source];
              source_queue[source][slot] = new_payload;
              source_arrival_cycle[source][slot] = wall_cycle;
              queue_tail[source] = (queue_tail[source] + 1) % QDEPTH;
              queue_count[source] = queue_count[source] + 1;
            end
          end
        end
        workload_cycle = workload_cycle + 1;
      end

      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        if (!pending_valid[source] && (queue_count[source] > 0)) begin
          slot = queue_head[source];
          pending_payload[source] = source_queue[source][slot];
          pending_arrival_cycle[source] = source_arrival_cycle[source][slot];
          pending_valid[source] = 1'b1;
          queue_head[source] = (queue_head[source] + 1) % QDEPTH;
          queue_count[source] = queue_count[source] - 1;
        end
        in_valid[source] = pending_valid[source];
        in_addr[source] = pending_payload[source];
      end
    end
  end

  always @(posedge clk or negedge rst_n) begin : scoreboard
    integer source;
    integer accepted_this_cycle;
    integer accepted_source_index;
    integer fabric_latency;
    integer end_to_end_latency;
    integer gap;
    logic [NUM_SOURCES-1:0] handshakes;
    if (!rst_n) begin
      accepted_pulse = '0;
      previous_output_stall = 1'b0;
      expected_priority = '0;
      for (source = 0; source < NUM_SOURCES; source = source + 1)
        previous_input_stall[source] = 1'b0;
    end else begin
      handshakes = in_valid & in_ready;
      accepted_pulse = handshakes;
      accepted_this_cycle = one_count(handshakes);
      accepted_source_index = 0;

      if (dut.u_arbiter.priority_q !== expected_priority)
        record_failure("priority changed without input handshake");

      if (one_count(dut.grant_onehot) > 1)
        record_failure("grant is not onehot0");
      if (accepted_this_cycle > 1)
        record_failure("more than one input handshake in a cycle");

      if (previous_output_stall &&
          ((!out_valid) || out_addr !== stalled_output_addr ||
           out_source !== stalled_output_source))
        record_failure("output payload changed while stalled");
      previous_output_stall = out_valid && !out_ready;
      if (out_valid && !out_ready) begin
        stalled_output_addr = out_addr;
        stalled_output_source = out_source;
      end

      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        if (previous_input_stall[source] &&
            ((!in_valid[source]) || in_addr[source] !== stalled_input_addr[source]))
          record_failure("pending input changed before handshake");
        previous_input_stall[source] = in_valid[source] && !in_ready[source];
        if (in_valid[source] && !in_ready[source])
          stalled_input_addr[source] = in_addr[source];

        if (handshakes[source]) begin
          accepted_source_index = source;
          if (expected_tail >= SCOREBOARD_DEPTH) begin
            record_failure("scoreboard overflow");
          end else begin
            expected_source[expected_tail] = SOURCE_WIDTH'(source);
            expected_payload[expected_tail] = in_addr[source];
            expected_accept_cycle[expected_tail] = wall_cycle;
            expected_arrival_cycle[expected_tail] = pending_arrival_cycle[source];
            expected_tail = expected_tail + 1;
          end
          accepted_count = accepted_count + 1;
          accepted_by_source[source] = accepted_by_source[source] + 1;
          service_ordinal = service_ordinal + 1;
          if (service_seen[source]) begin
            gap = service_ordinal - service_last_ordinal[source];
            if (gap > max_service_gap)
              max_service_gap = gap;
          end
          service_seen[source] = 1'b1;
          service_last_ordinal[source] = service_ordinal;
          if (first_accept_cycle < 0)
            first_accept_cycle = wall_cycle;
        end
      end

      if (accepted_this_cycle == 1) begin
        if (accepted_source_index == NUM_SOURCES - 1)
          expected_priority = '0;
        else
          expected_priority = SOURCE_WIDTH'(accepted_source_index + 1);
      end

      if (out_valid && out_ready) begin
        if (expected_head >= expected_tail) begin
          phantom_count = phantom_count + 1;
          duplicate_count = duplicate_count + 1;
          record_failure("phantom or duplicate output");
        end else begin
          if ((last_emitted_sequence[out_source] >= 0) &&
              (out_addr[SEQUENCE_WIDTH-1:0] <=
               SEQUENCE_WIDTH'(last_emitted_sequence[out_source]))) begin
            duplicate_count = duplicate_count + 1;
            record_failure("duplicate or non-increasing per-source sequence");
          end
          if (out_source !== expected_source[expected_head]) begin
            reorder_count = reorder_count + 1;
            record_failure("source reorder");
          end
          if (out_addr !== expected_payload[expected_head]) begin
            corruption_count = corruption_count + 1;
            record_failure("payload corruption or reorder");
          end
          fabric_latency = wall_cycle - expected_accept_cycle[expected_head];
          end_to_end_latency = wall_cycle - expected_arrival_cycle[expected_head];
          fabric_latency_sum = fabric_latency_sum + fabric_latency;
          end_to_end_latency_sum = end_to_end_latency_sum + end_to_end_latency;
          if (fabric_latency > fabric_latency_max)
            fabric_latency_max = fabric_latency;
          if (end_to_end_latency > end_to_end_latency_max)
            end_to_end_latency_max = end_to_end_latency;
          emitted_by_source[out_source] = emitted_by_source[out_source] + 1;
          if (observation_window_open) begin
            window_emitted_count = window_emitted_count + 1;
            window_latency_sum = window_latency_sum + end_to_end_latency;
            if (end_to_end_latency > window_latency_max)
              window_latency_max = end_to_end_latency;
            window_emitted_by_source[out_source] =
              window_emitted_by_source[out_source] + 1;
          end
          last_emitted_sequence[out_source] = out_addr[SEQUENCE_WIDTH-1:0];
          expected_head = expected_head + 1;
          emitted_count = emitted_count + 1;
          last_emit_cycle = wall_cycle;
        end
      end

      if ((accepted_count - emitted_count) < 0 ||
          (accepted_count - emitted_count) > 2)
        record_failure("A23 occupancy outside zero to two");

      // Kanghee's original benches stop immediately after CYCLES iterations.
      // Capture that same observation window before the compatibility harness
      // drains its queues for stronger loss/duplicate checks.
      if (observation_window_open && (workload_cycle >= cycles)) begin
        observation_window_open = 1'b0;
        window_overflow_count = overflow_count;
      end
    end
  end

  task automatic report_window;
    integer source;
    integer source_sum;
    real source_squares;
    real jain_fairness;
    integer average_latency;
    begin
      source_sum = 0;
      source_squares = 0.0;
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        source_sum = source_sum + window_emitted_by_source[source];
        source_squares = source_squares +
          real'(window_emitted_by_source[source] * window_emitted_by_source[source]);
      end
      if ((source_sum > 0) && (source_squares > 0.0))
        jain_fairness = real'(source_sum * source_sum) /
          (NUM_SOURCES * source_squares);
      else
        jain_fairness = 1.0;
      if (window_emitted_count > 0)
        average_latency = window_latency_sum / window_emitted_count;
      else
        average_latency = 0;
      $display("GANGHEE_A23_WINDOW seed=%0d N=%0d workload=%s hotspot_set=%s backpressure=%s cycles=%0d qdepth=%0d emitted=%0d overflow=%0d avg_latency=%0d max_latency=%0d throughput_per_cycle=%0.6f jain_x1000=%0d",
        seed, NUM_SOURCES, workload, hotspot_set, backpressure, cycles, QDEPTH,
        window_emitted_count, window_overflow_count, average_latency,
        window_latency_max, real'(window_emitted_count) / cycles,
        integer'(jain_fairness * 1000.0));
    end
  endtask

  task automatic report_result;
    integer source;
    integer source_sum;
    real source_squares;
    real jain_fairness;
    real fabric_latency_average;
    real end_to_end_latency_average;
    real throughput;
    begin
      source_sum = 0;
      source_squares = 0.0;
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        source_sum = source_sum + emitted_by_source[source];
        source_squares = source_squares +
          real'(emitted_by_source[source] * emitted_by_source[source]);
        $display("GANGHEE_A23_SOURCE seed=%0d N=%0d workload=%s source=%0d row=%0d col=%0d accepted=%0d emitted=%0d",
          seed, NUM_SOURCES, workload, source, source_row(source), source_col(source),
          accepted_by_source[source], emitted_by_source[source]);
      end
      if (emitted_count > 0) begin
        fabric_latency_average = real'(fabric_latency_sum) / emitted_count;
        end_to_end_latency_average = real'(end_to_end_latency_sum) / emitted_count;
      end else begin
        fabric_latency_average = 0.0;
        end_to_end_latency_average = 0.0;
      end
      if ((source_sum > 0) && (source_squares > 0.0))
        jain_fairness = real'(source_sum * source_sum) /
          (NUM_SOURCES * source_squares);
      else
        jain_fairness = 1.0;
      if (wall_cycle > 0)
        throughput = real'(emitted_count) / wall_cycle;
      else
        throughput = 0.0;

      $display("GANGHEE_A23_METRIC seed=%0d N=%0d workload=%s hotspot_set=%s backpressure=%s arrival_pct=%0d background_pct=%0d hotspot_pct=%0d phase_len=%0d cycles=%0d qdepth=%0d generated=%0d overflow=%0d accepted=%0d emitted=%0d inflight=%0d phantom=%0d duplicate=%0d loss=%0d reorder=%0d corruption=%0d fabric_latency_avg=%0.4f fabric_latency_max=%0d e2e_latency_avg=%0.4f e2e_latency_max=%0d throughput=%0.6f service_gap=%0d jain=%0.6f",
        seed, NUM_SOURCES, workload, hotspot_set, backpressure, arrival_pct,
        background_pct, hotspot_pct, phase_len, cycles, QDEPTH, generated_count,
        overflow_count, accepted_count, emitted_count, accepted_count - emitted_count,
        phantom_count, duplicate_count, generated_count - overflow_count - emitted_count,
        reorder_count, corruption_count,
        fabric_latency_average, fabric_latency_max, end_to_end_latency_average,
        end_to_end_latency_max, throughput, max_service_gap, jain_fairness);
    end
  endtask

  initial begin : test_control
    integer source;
    integer timeout;
    integer pending_total;
    string wave_path;
    if (!$value$plusargs("SEED=%d", seed)) seed = 1;
    if (!$value$plusargs("CYCLES=%d", cycles))
      cycles = (NUM_SOURCES == 64) ? 6000 : 3000;
    if (!$value$plusargs("ARRIVAL_PCT=%d", arrival_pct)) arrival_pct = 5;
    if (!$value$plusargs("BACKGROUND_PCT=%d", background_pct))
      background_pct = (NUM_SOURCES == 64) ? 2 : 3;
    if (!$value$plusargs("HOTSPOT_PCT=%d", hotspot_pct))
      hotspot_pct = (NUM_SOURCES == 64) ? 20 : 50;
    if (!$value$plusargs("PHASE_LEN=%d", phase_len))
      phase_len = (NUM_SOURCES == 64) ? 1500 : 400;
    if (!$value$plusargs("LONG_STALL_START=%d", long_stall_start))
      long_stall_start = cycles / 3;
    if (!$value$plusargs("LONG_STALL_CYCLES=%d", long_stall_cycles))
      long_stall_cycles = 100;
    if (!$value$plusargs("WORKLOAD=%s", workload)) workload = "uniform";
    if (!$value$plusargs("HOTSPOT_SET=%s", hotspot_set)) hotspot_set = "center";
    if (!$value$plusargs("BACKPRESSURE=%s", backpressure)) backpressure = "always";
    if ($value$plusargs("WAVE=%s", wave_path)) begin
      $dumpfile(wave_path);
      $dumpvars(0, ganghee_a23_compat_tb);
    end
    if ((NUM_SOURCES != 16) && (NUM_SOURCES != 64))
      $fatal(1, "NUM_SOURCES must be 16 or 64 for Ganghee compatibility");
    if (phase_len < 1)
      $fatal(1, "PHASE_LEN must be positive");

    rng_seed = seed;
    backpressure_rng_state = seed ^ 32'h9e3779b9;
    clear_state();
    repeat (4) @(posedge clk);
    @(negedge clk);
    rst_n = 1'b1;

    timeout = 0;
    pending_total = 1;
    while (((workload_cycle < cycles) || (pending_total != 0) ||
            (accepted_count != emitted_count)) &&
           (timeout < cycles + NUM_SOURCES * (QDEPTH + 8) + long_stall_cycles + 10000)) begin
      @(negedge clk);
      pending_total = 0;
      for (source = 0; source < NUM_SOURCES; source = source + 1)
        pending_total = pending_total + queue_count[source] + pending_valid[source];
      timeout = timeout + 1;
    end
    repeat (3) @(negedge clk);

    if (timeout >= cycles + NUM_SOURCES * (QDEPTH + 8) + long_stall_cycles + 10000)
      record_failure("timeout draining workload and A23 pipeline");
    if (accepted_count != emitted_count)
      record_failure("accepted/emitted mismatch at end of test");
    if (expected_head != expected_tail)
      record_failure("reference queue not empty at end of test");
    if ((generated_count - overflow_count) != accepted_count)
      record_failure("non-overflow arrivals were not all accepted");

    report_window();
    report_result();
    if (failures != 0)
      $fatal(1, "GANGHEE_A23_RESULT FAIL seed=%0d N=%0d workload=%s failures=%0d",
        seed, NUM_SOURCES, workload, failures);
    $display("GANGHEE_A23_RESULT PASS seed=%0d N=%0d workload=%s backpressure=%s",
      seed, NUM_SOURCES, workload, backpressure);
    $finish;
  end
endmodule
