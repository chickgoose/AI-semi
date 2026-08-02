`timescale 1ns/1ps

module a23_stress_tb;
  parameter int NUM_SOURCES = 4;
  parameter int ADDR_WIDTH = 16;
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES);
  parameter int MAX_EVENTS = 8192;

  logic clk = 1'b0;
  logic rst_n = 1'b0;
  always #5 clk = ~clk;

  logic [NUM_SOURCES-1:0] in_valid;
  logic [NUM_SOURCES-1:0] in_ready;
  logic [NUM_SOURCES-1:0][ADDR_WIDTH-1:0] in_addr;
  logic out_valid;
  logic out_ready;
  logic [ADDR_WIDTH-1:0] out_addr;
  logic [SOURCE_WIDTH-1:0] out_src;

  logic [SOURCE_WIDTH-1:0] expected_src [0:MAX_EVENTS-1];
  logic [ADDR_WIDTH-1:0] expected_addr [0:MAX_EVENTS-1];
  integer accepted_cycle [0:MAX_EVENTS-1];
  integer head;
  integer tail;
  integer accepted_count;
  integer emitted_count;
  integer cycle_count;
  integer latency_sum;
  integer max_latency;
  integer first_accept_cycle;
  integer last_emit_cycle;
  integer previous_accept_cycle;
  integer previous_emit_cycle;
  integer min_input_ii;
  integer max_input_ii;
  integer min_output_ii;
  integer max_output_ii;
  integer wait_count [NUM_SOURCES];
  integer max_wait;
  integer emitted_by_source [NUM_SOURCES];
  integer contention_service [NUM_SOURCES];
  integer contention_last_ordinal [NUM_SOURCES];
  logic contention_seen [NUM_SOURCES];
  integer contention_ordinal;
  integer max_service_gap;
  logic contention_check;
  logic previous_output_stalled;
  logic [ADDR_WIDTH-1:0] stalled_output_addr;
  logic [SOURCE_WIDTH-1:0] stalled_output_src;
  logic [NUM_SOURCES-1:0] previous_input_stalled;
  logic [ADDR_WIDTH-1:0] stalled_input_addr [NUM_SOURCES];
  logic [SOURCE_WIDTH-1:0] expected_priority;
  integer triple_count;
  integer checked_triple_count;
  integer failures;
  integer phase_failures;
  integer seed;
  logic [31:0] rng_state;
  string phase_name;
  logic reset_ready_seen;
  logic [ADDR_WIDTH-1:0] last_accepted_addr [NUM_SOURCES];
  logic accepted_addr_seen [NUM_SOURCES];

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
    .event_source_o(out_src)
  );

  function automatic integer count_ones(input logic [NUM_SOURCES-1:0] value);
    integer source;
    begin
      count_ones = 0;
      for (source = 0; source < NUM_SOURCES; source = source + 1)
        count_ones = count_ones + value[source];
    end
  endfunction

  function automatic logic [31:0] random_next(input logic [31:0] value);
    logic [31:0] next_value;
    begin
      next_value = value;
      next_value = next_value ^ (next_value << 13);
      next_value = next_value ^ (next_value >> 17);
      next_value = next_value ^ (next_value << 5);
      if (next_value == 0)
        next_value = 32'h1;
      random_next = next_value;
    end
  endfunction

  function automatic logic [ADDR_WIDTH-1:0] event_address(
    input integer source,
    input integer item,
    input integer phase_tag
  );
    logic [31:0] value;
    begin
      value = (phase_tag << 12) ^ (source << 9) ^ item;
      event_address = ADDR_WIDTH'(value);
    end
  endfunction

  task automatic record_failure(input string reason);
    begin
      $display("A23_STRESS_FAIL seed=%0d sources=%0d phase=%s cycle=%0d reason=%s",
               seed, NUM_SOURCES, phase_name, cycle_count, reason);
      failures = failures + 1;
      phase_failures = phase_failures + 1;
    end
  endtask

  task automatic inputs_clear;
    integer source;
    begin
      in_valid = '0;
      for (source = 0; source < NUM_SOURCES; source = source + 1)
        in_addr[source] = '0;
    end
  endtask

  task automatic phase_start(input string name);
    begin
      phase_name = name;
      phase_failures = 0;
      contention_check = 1'b0;
      @(negedge clk);
      rst_n = 1'b0;
      out_ready = 1'b0;
      inputs_clear();
      repeat (3) @(posedge clk);
      @(negedge clk);
      if (out_valid !== 1'b0)
        record_failure("out_valid remained set during reset");
      rst_n = 1'b1;
      out_ready = 1'b1;
    end
  endtask

  task automatic drain;
    integer timeout;
    begin
      @(negedge clk);
      out_ready = 1'b1;
      timeout = 0;
      while (((accepted_count != emitted_count) || out_valid) && timeout < 30000) begin
        @(negedge clk);
        timeout = timeout + 1;
      end
      if (timeout >= 30000)
        record_failure("drain timeout");
      if (head != tail)
        record_failure("reference queue not empty after drain");
    end
  endtask

  task automatic phase_report;
    integer source;
    integer span;
    real average_latency;
    real throughput;
    real sum;
    real squares;
    real fairness;
    begin
      if (emitted_count == 0)
        average_latency = 0.0;
      else
        average_latency = real'(latency_sum) / emitted_count;
      span = last_emit_cycle - first_accept_cycle + 1;
      if ((emitted_count == 0) || (span <= 0))
        throughput = 0.0;
      else
        throughput = real'(emitted_count) / span;
      sum = 0.0;
      squares = 0.0;
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        sum = sum + emitted_by_source[source];
        squares = squares + emitted_by_source[source] * emitted_by_source[source];
      end
      if (squares == 0.0)
        fairness = 1.0;
      else
        fairness = (sum * sum) / (NUM_SOURCES * squares);
      $display("A23_STRESS_METRIC seed=%0d sources=%0d phase=%s accepted=%0d emitted=%0d failures=%0d avg_latency=%0.4f max_latency=%0d throughput=%0.6f input_ii_min=%0d input_ii_max=%0d output_ii_min=%0d output_ii_max=%0d service_gap=%0d max_wait=%0d fairness=%0.6f reset_ready=%0d triple=%0d",
        seed, NUM_SOURCES, phase_name, accepted_count, emitted_count,
        phase_failures, average_latency, max_latency, throughput,
        (min_input_ii == 1000000) ? 0 : min_input_ii, max_input_ii,
        (min_output_ii == 1000000) ? 0 : min_output_ii, max_output_ii,
        max_service_gap, max_wait, fairness, reset_ready_seen, triple_count);
    end
  endtask

  task automatic single_source_stream(
    input integer source,
    input integer event_count,
    input integer phase_tag
  );
    integer sent;
    begin
      sent = 0;
      @(negedge clk);
      in_valid[source] = 1'b1;
      in_addr[source] = event_address(source, sent, phase_tag);
      while (sent < event_count) begin
        @(posedge clk);
        if (in_valid[source] && in_ready[source])
          sent = sent + 1;
        @(negedge clk);
        if (sent < event_count)
          in_addr[source] = event_address(source, sent, phase_tag);
        else begin
          in_valid[source] = 1'b0;
          in_addr[source] = '0;
        end
      end
    end
  endtask

  task automatic source_bursts(input logic unequal_lengths, input integer phase_tag);
    integer remaining [NUM_SOURCES];
    integer item [NUM_SOURCES];
    logic [NUM_SOURCES-1:0] handshakes;
    integer source;
    integer active;
    begin
      @(negedge clk);
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        if (unequal_lengths)
          remaining[source] = 19 + source * 13;
        else
          remaining[source] = 64;
        item[source] = 0;
        in_valid[source] = 1'b1;
        in_addr[source] = event_address(source, 0, phase_tag);
      end
      active = NUM_SOURCES;
      while (active != 0) begin
        @(posedge clk);
        handshakes = in_valid & in_ready;
        @(negedge clk);
        for (source = 0; source < NUM_SOURCES; source = source + 1) begin
          if (handshakes[source]) begin
            remaining[source] = remaining[source] - 1;
            item[source] = item[source] + 1;
            if (remaining[source] == 0) begin
              in_valid[source] = 1'b0;
              in_addr[source] = '0;
            end else begin
              in_addr[source] = event_address(source, item[source], phase_tag);
            end
          end
        end
        active = count_ones(in_valid);
      end
    end
  endtask

  task automatic random_traffic(input integer event_target, input logic use_backpressure);
    integer issued;
    integer item;
    integer source;
    logic [NUM_SOURCES-1:0] handshakes;
    begin
      issued = 0;
      item = 0;
      handshakes = '0;
      @(negedge clk);
      while ((issued < event_target) || (|in_valid)) begin
        for (source = 0; source < NUM_SOURCES; source = source + 1) begin
          if (handshakes[source]) begin
            in_valid[source] = 1'b0;
            in_addr[source] = '0;
          end
        end
        rng_state = random_next(rng_state);
        if (use_backpressure)
          out_ready = rng_state[0] || rng_state[3];
        else
          out_ready = 1'b1;
        for (source = 0; source < NUM_SOURCES; source = source + 1) begin
          rng_state = random_next(rng_state);
          if (!in_valid[source] && issued < event_target && rng_state[0]) begin
            in_valid[source] = 1'b1;
            in_addr[source] = event_address(source, item, 5);
            issued = issued + 1;
            item = item + 1;
          end
        end
        if (!(|in_valid) && issued < event_target) begin
          source = issued % NUM_SOURCES;
          in_valid[source] = 1'b1;
          in_addr[source] = event_address(source, item, 5);
          issued = issued + 1;
          item = item + 1;
        end
        @(posedge clk);
        handshakes = in_valid & in_ready;
        @(negedge clk);
      end
      out_ready = 1'b1;
    end
  endtask

  task automatic alternating_ready(input integer event_count);
    logic source_active;
    logic ready_value;
    begin
      source_active = 1'b1;
      ready_value = 1'b0;
      fork
        begin
          single_source_stream(0, event_count, 6);
          source_active = 1'b0;
        end
        begin
          while (source_active) begin
            @(negedge clk);
            out_ready = ready_value;
            ready_value = !ready_value;
          end
        end
      join
      @(negedge clk);
      out_ready = 1'b1;
    end
  endtask

  task automatic long_stall_full_boundary(input integer stall_cycles);
    integer timeout;
    begin
      @(negedge clk);
      out_ready = 1'b0;
      fork
        single_source_stream(0, 48, 7);
        begin
          timeout = 0;
          while (((accepted_count - emitted_count) != 2) && timeout < 100) begin
            @(negedge clk);
            timeout = timeout + 1;
          end
          if (timeout >= 100)
            record_failure("pipeline failed to reach full occupancy");
          repeat (stall_cycles) @(posedge clk);
          @(negedge clk);
          out_ready = 1'b1;
        end
      join
    end
  endtask

  task automatic reset_valid_boundary;
    begin
      phase_name = "reset_valid_boundary";
      phase_failures = 0;
      contention_check = 1'b0;
      @(negedge clk);
      out_ready = 1'b0;
      in_valid[0] = 1'b1;
      in_addr[0] = event_address(0, 0, 8);
      @(posedge clk);
      @(negedge clk);
      in_addr[0] = event_address(0, 1, 8);
      @(posedge clk);
      @(negedge clk);
      // Keep valid asserted across reset, but present a fresh event after the
      // two pre-reset handshakes so every accepted address remains unique.
      in_addr[0] = event_address(0, 2, 8);
      rst_n = 1'b0;
      repeat (4) begin
        @(posedge clk);
        if (in_ready[0])
          reset_ready_seen = 1'b1;
      end
      @(negedge clk);
      if (out_valid !== 1'b0)
        record_failure("reset failed to clear output while input valid held");
      out_ready = 1'b1;
      rst_n = 1'b1;
      @(posedge clk);
      if (!(in_valid[0] && in_ready[0]))
        record_failure("held valid did not handshake immediately after reset");
      @(negedge clk);
      in_valid[0] = 1'b0;
      in_addr[0] = '0;
      drain();
      if (accepted_count != 1 || emitted_count != 1)
        record_failure("post-reset event missing or duplicated");
      if (first_accept_cycle != 1)
        record_failure("post-reset handshake was not on cycle one");
      phase_report();
    end
  endtask

  always @(posedge clk or negedge rst_n) begin : scoreboard
    integer source;
    integer accepted_this;
    integer accepted_source;
    integer latency;
    integer gap;
    logic [NUM_SOURCES-1:0] handshakes;
    if (!rst_n) begin
      head = 0;
      tail = 0;
      accepted_count = 0;
      emitted_count = 0;
      cycle_count = 0;
      latency_sum = 0;
      max_latency = 0;
      first_accept_cycle = -1;
      last_emit_cycle = -1;
      previous_accept_cycle = -1;
      previous_emit_cycle = -1;
      min_input_ii = 1000000;
      max_input_ii = 0;
      min_output_ii = 1000000;
      max_output_ii = 0;
      max_wait = 0;
      contention_ordinal = 0;
      max_service_gap = 0;
      previous_output_stalled = 1'b0;
      previous_input_stalled = '0;
      expected_priority = '0;
      triple_count = 0;
      checked_triple_count = 0;
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        wait_count[source] = 0;
        emitted_by_source[source] = 0;
        contention_service[source] = 0;
        contention_last_ordinal[source] = 0;
        contention_seen[source] = 1'b0;
        accepted_addr_seen[source] = 1'b0;
        last_accepted_addr[source] = '0;
      end
    end else begin
      cycle_count = cycle_count + 1;
      handshakes = in_valid & in_ready;
      accepted_this = count_ones(handshakes);
      accepted_source = 0;

      if (count_ones(dut.grant_onehot) > 1)
        record_failure("arbiter grant violated onehot0");
      if (dut.grant_valid != (|dut.grant_onehot))
        record_failure("grant_valid disagreed with grant vector");
      if (dut.u_arbiter.priority_q >= NUM_SOURCES)
        record_failure("priority state outside source range");
      if (accepted_this > 1)
        record_failure("multiple input handshakes in one cycle");
      if (dut.input_handshake != (accepted_this == 1))
        record_failure("internal input_handshake disagreed with interface handshake");

      if (previous_output_stalled &&
          ((out_valid !== 1'b1) || (out_addr !== stalled_output_addr) ||
           (out_src !== stalled_output_src)))
        record_failure("output changed while stalled");
      previous_output_stalled = out_valid && !out_ready;
      if (out_valid && !out_ready) begin
        stalled_output_addr = out_addr;
        stalled_output_src = out_src;
      end

      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        if (previous_input_stalled[source] &&
            ((!in_valid[source]) || (in_addr[source] !== stalled_input_addr[source])))
          record_failure("producer changed valid/address before handshake");
        previous_input_stalled[source] = in_valid[source] && !in_ready[source];
        if (in_valid[source] && !in_ready[source])
          stalled_input_addr[source] = in_addr[source];

        if (handshakes[source]) begin
          accepted_source = source;
          if (tail >= MAX_EVENTS) begin
            record_failure("reference queue overflow");
          end else begin
            if (accepted_addr_seen[source] &&
                last_accepted_addr[source] == in_addr[source])
              record_failure("address did not change at source handshake");
            accepted_addr_seen[source] = 1'b1;
            last_accepted_addr[source] = in_addr[source];
            expected_src[tail] = SOURCE_WIDTH'(source);
            expected_addr[tail] = in_addr[source];
            accepted_cycle[tail] = cycle_count;
            tail = tail + 1;
            accepted_count = accepted_count + 1;
            if (first_accept_cycle < 0)
              first_accept_cycle = cycle_count;
            if (previous_accept_cycle >= 0) begin
              gap = cycle_count - previous_accept_cycle;
              if (gap < min_input_ii) min_input_ii = gap;
              if (gap > max_input_ii) max_input_ii = gap;
            end
            previous_accept_cycle = cycle_count;
          end
          if (contention_check) begin
            contention_ordinal = contention_ordinal + 1;
            contention_service[source] = contention_service[source] + 1;
            if (contention_seen[source])
              gap = contention_ordinal - contention_last_ordinal[source];
            else
              gap = contention_ordinal;
            if (gap > max_service_gap)
              max_service_gap = gap;
            if (gap > NUM_SOURCES)
              record_failure("fairness service gap exceeded NUM_SOURCES");
            contention_seen[source] = 1'b1;
            contention_last_ordinal[source] = contention_ordinal;
          end
        end
        if (in_valid[source] && !in_ready[source]) begin
          wait_count[source] = wait_count[source] + 1;
          if (wait_count[source] > max_wait)
            max_wait = wait_count[source];
        end else begin
          wait_count[source] = 0;
        end
      end

      if (accepted_this == 1) begin
        if (accepted_source == NUM_SOURCES - 1)
          expected_priority = '0;
        else
          expected_priority = SOURCE_WIDTH'(accepted_source + 1);
      end else begin
          expected_priority = dut.u_arbiter.priority_q;
      end

      if (out_valid && out_ready) begin
        if (head >= tail) begin
          record_failure("duplicate or unexpected output");
        end else begin
          if ((out_src !== expected_src[head]) ||
              (out_addr !== expected_addr[head]))
            record_failure("output reorder or source/address corruption");
          latency = cycle_count - accepted_cycle[head];
          latency_sum = latency_sum + latency;
          if (latency > max_latency)
            max_latency = latency;
          if (previous_emit_cycle >= 0) begin
            gap = cycle_count - previous_emit_cycle;
            if (gap < min_output_ii) min_output_ii = gap;
            if (gap > max_output_ii) max_output_ii = gap;
          end
          previous_emit_cycle = cycle_count;
          last_emit_cycle = cycle_count;
          emitted_by_source[out_src] = emitted_by_source[out_src] + 1;
          head = head + 1;
          emitted_count = emitted_count + 1;
        end
      end

      if ((accepted_count - emitted_count) < 0 ||
          (accepted_count - emitted_count) > 2)
        record_failure("logical occupancy outside zero to two");
      if (dut.u_tx.full_q && dut.u_rx.full_q &&
          out_valid && out_ready && accepted_this == 1)
        triple_count = triple_count + 1;
    end
  end

  always @(negedge clk) begin : state_checks
    integer logical_occupancy;
    integer physical_occupancy;
    if (rst_n) begin
      logical_occupancy = accepted_count - emitted_count;
      physical_occupancy = dut.u_tx.full_q + dut.u_rx.full_q;
      if (logical_occupancy != physical_occupancy)
        record_failure("accepted-emitted disagreed with TX/RX occupancy");
      if (physical_occupancy < 0 || physical_occupancy > 2)
        record_failure("physical occupancy outside zero to two");
      if (dut.u_arbiter.priority_q !== expected_priority)
        record_failure("priority changed without handshake or advanced incorrectly");
      if (checked_triple_count != triple_count) begin
        if (!(dut.u_tx.full_q && dut.u_rx.full_q &&
              logical_occupancy == 2))
          record_failure("simultaneous drain/refill/accept lost pipeline occupancy");
        checked_triple_count = triple_count;
      end
    end
  end

  initial begin : test_sequence
    integer source;
    string wave_path;
    if (!$value$plusargs("SEED=%d", seed))
      seed = 1;
    if ($value$plusargs("WAVE=%s", wave_path)) begin
      $dumpfile(wave_path);
      $dumpvars(0, a23_stress_tb);
    end
    rng_state = seed;
    failures = 0;
    phase_failures = 0;
    phase_name = "startup";
    reset_ready_seen = 1'b0;
    contention_check = 1'b0;
    inputs_clear();
    out_ready = 1'b0;

    phase_start("single_source_service");
    single_source_stream(seed % NUM_SOURCES, 96, 1);
    drain();
    if (max_input_ii != 1 || max_output_ii != 1 || max_latency != 2)
      record_failure("single-source normal service failed II=1/latency=2");
    phase_report();

    phase_start("all_sources_service");
    contention_check = 1'b1;
    source_bursts(1'b0, 2);
    contention_check = 1'b0;
    drain();
    for (source = 0; source < NUM_SOURCES; source = source + 1)
      if (contention_service[source] != 64)
        record_failure("source did not receive 64 services under contention");
    if (max_service_gap > NUM_SOURCES || max_latency != 2)
      record_failure("normal-service fairness or latency bound failed");
    phase_report();

    phase_start("unequal_bursts");
    source_bursts(1'b1, 3);
    drain();
    phase_report();

    phase_start("random_valid_address");
    random_traffic(384, 1'b0);
    drain();
    phase_report();

    phase_start("random_backpressure");
    random_traffic(512, 1'b1);
    drain();
    phase_report();

    phase_start("alternating_ready_0101");
    alternating_ready(160);
    drain();
    phase_report();

    phase_start("long_stall_full_drain_refill");
    long_stall_full_boundary(80 + (seed % 17));
    drain();
    if (triple_count == 0)
      record_failure("full-boundary simultaneous drain/refill was not exercised");
    phase_report();

    reset_valid_boundary();

    if (failures != 0)
      $fatal(1, "A23_STRESS_RESULT FAIL seed=%0d sources=%0d failures=%0d",
             seed, NUM_SOURCES, failures);
    $display("A23_STRESS_RESULT PASS seed=%0d sources=%0d reset_ready_seen=%0d",
             seed, NUM_SOURCES, reset_ready_seen);
    $finish;
  end
endmodule
