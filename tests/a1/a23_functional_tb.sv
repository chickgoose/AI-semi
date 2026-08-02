`timescale 1ns/1ps

module a23_functional_tb;
  parameter int NUM_SOURCES = 4;
  parameter int ADDR_WIDTH = 16;
  parameter int EVENTS = 128;
  parameter int TIMEOUT_CYCLES = 10000;
  localparam int SOURCE_WIDTH = aer_pkg::index_width(NUM_SOURCES);
  localparam int QUEUE_DEPTH = 2048;

  logic clk = 1'b0;
  logic rst_n = 1'b0;
  always #5 clk = ~clk;

  logic [NUM_SOURCES-1:0] src_valid;
  logic [NUM_SOURCES-1:0] src_ready;
  logic [NUM_SOURCES-1:0][ADDR_WIDTH-1:0] src_addr;
  logic event_valid;
  logic event_ready;
  logic [ADDR_WIDTH-1:0] event_addr;
  logic [SOURCE_WIDTH-1:0] event_source;

  logic [ADDR_WIDTH-1:0] expected [NUM_SOURCES][QUEUE_DEPTH];
  integer head [NUM_SOURCES];
  integer tail [NUM_SOURCES];
  integer event_number [NUM_SOURCES];
  integer accepted;
  integer emitted;
  integer cycle_count;
  integer last_accept_cycle;
  integer last_emit_cycle;
  integer max_input_ii;
  integer max_output_ii;
  integer last_service_ordinal [NUM_SOURCES];
  integer max_service_gap;
  integer max_occupancy;
  integer reset_ready_high;
  logic [NUM_SOURCES-1:0] accepted_pulse;
  logic previous_stall;
  logic [ADDR_WIDTH-1:0] stalled_addr;
  logic [SOURCE_WIDTH-1:0] stalled_source;
  logic [SOURCE_WIDTH-1:0] previous_priority;
  logic [SOURCE_WIDTH-1:0] expected_priority;
  logic previous_input_fire;

  a23_ee430_core #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_INDEX_WIDTH(SOURCE_WIDTH)
  ) dut (
    .clk_i(clk),
    .rst_ni(rst_n),
    .src_valid_i(src_valid),
    .src_ready_o(src_ready),
    .src_addr_i(src_addr),
    .event_valid_o(event_valid),
    .event_ready_i(event_ready),
    .event_addr_o(event_addr),
    .event_source_o(event_source)
  );

  function automatic logic [ADDR_WIDTH-1:0] make_event(
    input integer source,
    input integer index
  );
    make_event = ADDR_WIDTH'((source << (ADDR_WIDTH/2)) ^ index ^ 16'h5a00);
  endfunction

  task automatic clear_inputs;
    integer source;
    begin
      src_valid = '0;
      event_ready = 1'b0;
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        src_addr[source] = '0;
      end
    end
  endtask

  task automatic apply_reset;
    begin
      @(negedge clk);
      rst_n = 1'b0;
      repeat (3) @(posedge clk);
      @(negedge clk);
      rst_n = 1'b1;
    end
  endtask

  task automatic wait_empty;
    integer watchdog;
    begin
      watchdog = 0;
      while ((accepted != emitted) || event_valid) begin
        @(negedge clk);
        src_valid = '0;
        event_ready = 1'b1;
        watchdog = watchdog + 1;
        if (watchdog > TIMEOUT_CYCLES) begin
          $fatal(1, "timeout draining accepted=%0d emitted=%0d", accepted, emitted);
        end
      end
    end
  endtask

  always @(posedge clk or negedge rst_n) begin : scoreboard
    integer source;
    integer ready_count;
    integer occupancy;
    integer input_ii;
    integer output_ii;
    integer service_gap;
    if (!rst_n) begin
      accepted = 0;
      emitted = 0;
      cycle_count = 0;
      last_accept_cycle = -1;
      last_emit_cycle = -1;
      max_input_ii = 0;
      max_output_ii = 0;
      max_service_gap = 0;
      max_occupancy = 0;
      accepted_pulse = '0;
      previous_stall = 1'b0;
      stalled_addr = '0;
      stalled_source = '0;
      previous_priority = '0;
      expected_priority = '0;
      previous_input_fire = 1'b0;
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        head[source] = 0;
        tail[source] = 0;
        event_number[source] = 0;
        last_service_ordinal[source] = 0;
      end
    end else begin
      cycle_count = cycle_count + 1;
      accepted_pulse = '0;
      ready_count = 0;

      if (previous_input_fire) begin
        if (dut.u_arbiter.priority_q !== expected_priority) begin
          $fatal(1, "priority did not advance on input handshake: got=%0d expected=%0d",
            dut.u_arbiter.priority_q, expected_priority);
        end
      end else if (dut.u_arbiter.priority_q !== previous_priority) begin
        $fatal(1, "priority changed without an input handshake: got=%0d previous=%0d",
          dut.u_arbiter.priority_q, previous_priority);
      end

      if (previous_stall) begin
        if (!event_valid || event_addr !== stalled_addr ||
            event_source !== stalled_source) begin
          $fatal(1, "stalled output changed at cycle %0d", cycle_count);
        end
      end

      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        if (src_valid[source] && src_ready[source]) begin
          ready_count = ready_count + 1;
          accepted_pulse[source] = 1'b1;
          if (tail[source] >= QUEUE_DEPTH) begin
            $fatal(1, "scoreboard overflow source=%0d", source);
          end
          expected[source][tail[source]] = src_addr[source];
          tail[source] = tail[source] + 1;
          accepted = accepted + 1;
          service_gap = accepted - last_service_ordinal[source];
          if (last_service_ordinal[source] != 0 && service_gap > max_service_gap)
            max_service_gap = service_gap;
          last_service_ordinal[source] = accepted;
          if (last_accept_cycle >= 0) begin
            input_ii = cycle_count - last_accept_cycle;
            if (input_ii > max_input_ii) max_input_ii = input_ii;
          end
          last_accept_cycle = cycle_count;
          if (source == NUM_SOURCES - 1)
            expected_priority = '0;
          else
            expected_priority = SOURCE_WIDTH'(source + 1);
        end
      end
      if (ready_count > 1 || !$onehot0(src_ready)) begin
        $fatal(1, "ready/grant is not onehot0 at cycle %0d ready=%b", cycle_count,
          src_ready);
      end
      if (!$onehot0(dut.grant_onehot)) begin
        $fatal(1, "arbiter grant is not onehot0 at cycle %0d grant=%b",
          cycle_count, dut.grant_onehot);
      end

      if (event_valid && event_ready) begin
        if (event_source >= NUM_SOURCES) begin
          $fatal(1, "invalid output source %0d", event_source);
        end
        if (head[event_source] >= tail[event_source]) begin
          $fatal(1, "unexpected/duplicate output source=%0d addr=%h",
            event_source, event_addr);
        end
        if (event_addr !== expected[event_source][head[event_source]]) begin
          $fatal(1, "corruption/reorder source=%0d got=%h expected=%h",
            event_source, event_addr, expected[event_source][head[event_source]]);
        end
        head[event_source] = head[event_source] + 1;
        emitted = emitted + 1;
        if (last_emit_cycle >= 0) begin
          output_ii = cycle_count - last_emit_cycle;
          if (output_ii > max_output_ii) max_output_ii = output_ii;
        end
        last_emit_cycle = cycle_count;
      end

      occupancy = accepted - emitted;
      if (occupancy < 0 || occupancy > 2) begin
        $fatal(1, "occupancy invariant failed: accepted=%0d emitted=%0d",
          accepted, emitted);
      end
      if (occupancy > max_occupancy) max_occupancy = occupancy;

      previous_stall = event_valid && !event_ready;
      if (event_valid && !event_ready) begin
        stalled_addr = event_addr;
        stalled_source = event_source;
      end
      previous_priority = dut.u_arbiter.priority_q;
      previous_input_fire = (ready_count == 1);
    end
  end

  initial begin : stimulus
    integer source;
    integer target;
    integer seed;
    integer rng_seed;
    integer random_value;
    integer accepted_before_stall;
    integer accepted_during_stall;
    integer continuous_max_input_ii;
    integer continuous_max_output_ii;
    integer continuous_service_gap;

    if (!$value$plusargs("SEED=%d", seed)) seed = 23001;
    rng_seed = seed;
    random_value = $urandom(rng_seed);
    reset_ready_high = 0;
    clear_inputs();

    // Phase 1: all sources continuously request. This checks one event/cycle
    // after fill and the round-robin handshake service bound.
    apply_reset();
    event_ready = 1'b1;
    for (source = 0; source < NUM_SOURCES; source = source + 1) begin
      src_valid[source] = 1'b1;
      src_addr[source] = make_event(source, event_number[source]);
    end
    target = NUM_SOURCES * 24;
    while (accepted < target) begin
      @(negedge clk);
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        if (accepted_pulse[source]) begin
          event_number[source] = event_number[source] + 1;
          src_addr[source] = make_event(source, event_number[source]);
        end
      end
    end
    src_valid = '0;
    wait_empty();
    continuous_max_input_ii = max_input_ii;
    continuous_max_output_ii = max_output_ii;
    continuous_service_gap = max_service_gap;
    if (continuous_max_input_ii != 1 || continuous_max_output_ii != 1) begin
      $fatal(1, "continuous II failed input=%0d output=%0d",
        continuous_max_input_ii, continuous_max_output_ii);
    end
    if (NUM_SOURCES > 1 && continuous_service_gap > NUM_SOURCES) begin
      $fatal(1, "service gap %0d exceeds %0d", continuous_service_gap, NUM_SOURCES);
    end

    // Phase 2: random producer activity and random sink backpressure. A valid
    // request and its address remain stable until the source handshakes.
    apply_reset();
    target = EVENTS;
    while (accepted < target) begin
      @(negedge clk);
      random_value = $urandom;
      event_ready = random_value[0] | random_value[3];
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        if (accepted_pulse[source]) src_valid[source] = 1'b0;
        if (!src_valid[source] && (accepted < target)) begin
          random_value = $urandom;
          if (random_value[0]) begin
            src_valid[source] = 1'b1;
            src_addr[source] = ADDR_WIDTH'(random_value);
            event_number[source] = event_number[source] + 1;
          end
        end
      end
    end
    src_valid = '0;
    event_ready = 1'b1;
    wait_empty();

    // Phase 3: fill both pipeline entries, hold a full 30-cycle stall, then
    // release while requests remain asserted to exercise drain+refill.
    apply_reset();
    event_ready = 1'b0;
    for (source = 0; source < NUM_SOURCES; source = source + 1) begin
      src_valid[source] = 1'b1;
      src_addr[source] = make_event(source, event_number[source]);
    end
    while (accepted < 2) begin
      @(negedge clk);
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        if (accepted_pulse[source]) begin
          event_number[source] = event_number[source] + 1;
          src_addr[source] = make_event(source, event_number[source]);
        end
      end
    end
    accepted_before_stall = accepted;
    repeat (30) begin
      @(negedge clk);
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        if (accepted_pulse[source]) begin
          event_number[source] = event_number[source] + 1;
          src_addr[source] = make_event(source, event_number[source]);
        end
      end
    end
    accepted_during_stall = accepted - accepted_before_stall;
    if (accepted_during_stall != 0) begin
      $fatal(1, "accepted %0d events while two-entry pipeline was stalled",
        accepted_during_stall);
    end
    event_ready = 1'b1;
    target = accepted + NUM_SOURCES * 8;
    while (accepted < target) begin
      @(negedge clk);
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        if (accepted_pulse[source]) begin
          event_number[source] = event_number[source] + 1;
          src_addr[source] = make_event(source, event_number[source]);
        end
      end
    end
    src_valid = '0;
    wait_empty();

    // Phase 4: reset while valid is asserted. Pending state must flush and the
    // first post-reset request must transfer without stale output data.
    event_ready = 1'b0;
    src_valid = '1;
    for (source = 0; source < NUM_SOURCES; source = source + 1)
      src_addr[source] = make_event(source, event_number[source]);
    repeat (3) @(negedge clk);
    rst_n = 1'b0;
    repeat (3) @(posedge clk);
    // Sample away from reset assertion and the combinational delta-cycle.
    #1;
    if (|src_ready) reset_ready_high = 1;
    if (event_valid !== 1'b0) $fatal(1, "event_valid high during reset");
    @(negedge clk);
    event_ready = 1'b1;
    rst_n = 1'b1;
    target = NUM_SOURCES * 4;
    while (accepted < target) begin
      @(negedge clk);
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        if (accepted_pulse[source]) begin
          event_number[source] = event_number[source] + 1;
          src_addr[source] = make_event(source, event_number[source]);
        end
      end
    end
    src_valid = '0;
    wait_empty();

    $display("A23_FUNCTIONAL_PASS sources=%0d seed=%0d continuous_input_ii=%0d continuous_output_ii=%0d service_gap=%0d occupancy=%0d reset_ready_high=%0d",
      NUM_SOURCES, seed, continuous_max_input_ii, continuous_max_output_ii,
      continuous_service_gap, max_occupancy, reset_ready_high);
    $finish;
  end

  initial begin
    repeat (TIMEOUT_CYCLES) @(posedge clk);
    $fatal(1, "global timeout");
  end
endmodule
