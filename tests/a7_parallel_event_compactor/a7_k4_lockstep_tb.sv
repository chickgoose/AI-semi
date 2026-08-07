`timescale 1ns/1ps

// Candidate-only equivalence qualification.  The frozen common benchmark is
// deliberately not involved: this test drives the original shared-prefix K=4
// implementation and its equal-state replicated-selector reference in lockstep.
module a7_k4_lockstep_tb;
  localparam int N = 16;
  localparam int K = 4;
  localparam int AW = 16;
  localparam int SW = $clog2(N);
  localparam int MAX_ACCEPTED_PER_SOURCE = 4096;

  logic clk = 1'b0;
  always #5 clk = ~clk;

  logic rst_n;
  logic [N-1:0] source_valid;
  logic [N-1:0][AW-1:0] source_event;
  logic [K-1:0] retire_ready;

  logic [N-1:0] prefix_source_ready;
  logic [K-1:0] prefix_retire_valid;
  logic [K-1:0][AW-1:0] prefix_retire_event;
  logic [K-1:0][SW-1:0] prefix_retire_source;

  logic [N-1:0] reference_source_ready;
  logic [K-1:0] reference_retire_valid;
  logic [K-1:0][AW-1:0] reference_retire_event;
  logic [K-1:0][SW-1:0] reference_retire_source;

  logic [K-1:0] stalled_last_edge;
  logic [K-1:0][AW-1:0] stalled_event;
  logic [K-1:0][SW-1:0] stalled_source;
  logic [31:0] random_state;
  integer next_sequence [N];
  integer service_count [N];
  integer persistent_before [N];
  integer expected_head [N];
  integer expected_tail [N];
  logic [AW-1:0] expected_event [N][MAX_ACCEPTED_PER_SOURCE];
  integer accepted_count;
  integer delivered_count;
  integer cycle_count;
  integer source;
  integer lane;
  integer decoded_source;
  integer drain_cycles;

  a7_parallel_event_compactor #(
    .NUM_SOURCES(N), .ADDR_WIDTH(AW), .RETIRE_LANES(K)
  ) prefix (
    .clk, .rst_n, .source_valid, .source_event,
    .source_ready(prefix_source_ready),
    .retire_valid(prefix_retire_valid),
    .retire_event(prefix_retire_event),
    .retire_source(prefix_retire_source), .retire_ready
  );

  a7_replicated_selector_reference #(
    .NUM_SOURCES(N), .ADDR_WIDTH(AW), .RETIRE_LANES(K)
  ) reference (
    .clk, .rst_n, .source_valid, .source_event,
    .source_ready(reference_source_ready),
    .retire_valid(reference_retire_valid),
    .retire_event(reference_retire_event),
    .retire_source(reference_retire_source), .retire_ready
  );

  function automatic logic [31:0] next_random(input logic [31:0] value);
    logic [31:0] mixed;
    begin
      mixed = value ^ (value << 13);
      mixed = mixed ^ (mixed >> 17);
      next_random = mixed ^ (mixed << 5);
    end
  endfunction

  function automatic logic [AW-1:0] make_payload(
    input integer source_index,
    input integer event_sequence
  );
    // Keep source identity visible while varying all low payload bits over time.
    make_payload = AW'((source_index << 12) | (event_sequence & 32'h0000_0fff));
  endfunction

  task automatic compare_interfaces;
    begin
      if (prefix_source_ready !== reference_source_ready)
        $fatal(1, "LOCKSTEP source_ready mismatch cycle=%0d prefix=%0h reference=%0h",
               cycle_count, prefix_source_ready, reference_source_ready);
      if (prefix_retire_valid !== reference_retire_valid)
        $fatal(1, "LOCKSTEP retire_valid mismatch cycle=%0d prefix=%0h reference=%0h",
               cycle_count, prefix_retire_valid, reference_retire_valid);
      if (prefix_retire_event !== reference_retire_event)
        $fatal(1, "LOCKSTEP retire_event mismatch cycle=%0d", cycle_count);
      if (prefix_retire_source !== reference_retire_source)
        $fatal(1, "LOCKSTEP retire_source mismatch cycle=%0d", cycle_count);
      for (lane = 0; lane < K; lane = lane + 1) begin
        if (stalled_last_edge[lane] &&
            (!prefix_retire_valid[lane] ||
             (prefix_retire_event[lane] !== stalled_event[lane]) ||
             (prefix_retire_source[lane] !== stalled_source[lane])))
          $fatal(1, "LOCKSTEP stalled valid/payload changed cycle=%0d lane=%0d",
                 cycle_count, lane);
      end
    end
  endtask

  task automatic retire_random_ready;
    begin
      for (lane = 0; lane < K; lane = lane + 1) begin
        random_state = next_random(random_state);
        retire_ready[lane] = random_state[0] | random_state[3];
      end
    end
  endtask

  task automatic refill_random_sources(input logic force_all);
    begin
      for (source = 0; source < N; source = source + 1) begin
        random_state = next_random(random_state);
        if (!source_valid[source] && (force_all || random_state[1:0] != 2'b00)) begin
          source_event[source] = make_payload(source, next_sequence[source]);
          next_sequence[source] = next_sequence[source] + 1;
          source_valid[source] = 1'b1;
        end
      end
    end
  endtask

  task automatic remember_stalls;
    begin
      for (lane = 0; lane < K; lane = lane + 1) begin
        stalled_last_edge[lane] =
          prefix_retire_valid[lane] && !retire_ready[lane];
        if (prefix_retire_valid[lane] && !retire_ready[lane]) begin
          stalled_event[lane] = prefix_retire_event[lane];
          stalled_source[lane] = prefix_retire_source[lane];
        end
      end
    end
  endtask

  // Sample handshakes in the active region, before either DUT updates its
  // registered lanes.  This is also the source-local ordering scoreboard.
  always @(posedge clk) begin
    if (rst_n) begin
      cycle_count = cycle_count + 1;
      for (source = 0; source < N; source = source + 1) begin
        if (source_valid[source] && prefix_source_ready[source]) begin
          if (expected_tail[source] >= MAX_ACCEPTED_PER_SOURCE)
            $fatal(1, "LOCKSTEP expected queue overflow source=%0d", source);
          expected_event[source][expected_tail[source]] = source_event[source];
          expected_tail[source] = expected_tail[source] + 1;
          service_count[source] = service_count[source] + 1;
          accepted_count = accepted_count + 1;
          source_valid[source] <= 1'b0;
        end
      end
      for (lane = 0; lane < K; lane = lane + 1) begin
        if (prefix_retire_valid[lane] && retire_ready[lane]) begin
          decoded_source = integer'(prefix_retire_source[lane]);
          if ((decoded_source < 0) || (decoded_source >= N))
            $fatal(1, "LOCKSTEP illegal retired source=%0d lane=%0d",
                   decoded_source, lane);
          if (expected_head[decoded_source] >= expected_tail[decoded_source])
            $fatal(1, "LOCKSTEP phantom/duplicate source=%0d lane=%0d",
                   decoded_source, lane);
          if (prefix_retire_event[lane] !==
              expected_event[decoded_source][expected_head[decoded_source]])
            $fatal(1, "LOCKSTEP source-local reorder/corruption source=%0d lane=%0d",
                   decoded_source, lane);
          expected_head[decoded_source] = expected_head[decoded_source] + 1;
          delivered_count = delivered_count + 1;
        end
      end
    end
  end

  initial begin
    rst_n = 1'b0;
    source_valid = '0;
    retire_ready = '0;
    stalled_last_edge = '0;
    stalled_event = '0;
    stalled_source = '0;
    random_state = 32'h7a11_c0de;
    accepted_count = 0;
    delivered_count = 0;
    cycle_count = 0;
    for (source = 0; source < N; source = source + 1) begin
      source_event[source] = '0;
      next_sequence[source] = 0;
      service_count[source] = 0;
      persistent_before[source] = 0;
      expected_head[source] = 0;
      expected_tail[source] = 0;
    end

    repeat (4) @(posedge clk);
    @(negedge clk);
    rst_n = 1'b1;

    // Random source occupancy/payload and independent per-lane backpressure.
    repeat (1024) begin
      @(negedge clk);
      compare_interfaces();
      refill_random_sources(1'b0);
      retire_random_ready();
      remember_stalls();
    end

    // Persistent full contention plus all-ready output forces bubble-free
    // same-edge retire/refill and gives every source repeated service.
    for (source = 0; source < N; source = source + 1)
      persistent_before[source] = service_count[source];
    repeat (96) begin
      @(negedge clk);
      compare_interfaces();
      refill_random_sources(1'b1);
      retire_ready = '1;
      remember_stalls();
    end
    for (source = 0; source < N; source = source + 1)
      if ((service_count[source] - persistent_before[source]) < 20)
        $fatal(1, "LOCKSTEP persistent service bound missed source=%0d delta=%0d",
               source, service_count[source] - persistent_before[source]);

    // A long, independently selected lane stall checks that other lanes keep
    // cycling while the held lane never drops valid or changes identity.
    repeat (96) begin
      @(negedge clk);
      compare_interfaces();
      refill_random_sources(1'b1);
      retire_ready = 4'b1110;
      remember_stalls();
    end

    // Stop new generation and drain both exactly equivalent machines.
    drain_cycles = 0;
    while (((source_valid != '0) || (prefix_retire_valid != '0) ||
            (accepted_count != delivered_count)) && (drain_cycles < 128)) begin
      @(negedge clk);
      compare_interfaces();
      retire_ready = '1;
      remember_stalls();
      drain_cycles = drain_cycles + 1;
    end
    @(negedge clk);
    compare_interfaces();

    if ((source_valid != '0) || (prefix_retire_valid != '0))
      $fatal(1, "LOCKSTEP drain timeout pending=%0h retire_valid=%0h ready=%0h accepted=%0d delivered=%0d",
             source_valid, prefix_retire_valid, prefix_source_ready,
             accepted_count, delivered_count);
    if (accepted_count != delivered_count)
      $fatal(1, "LOCKSTEP conservation failure accepted=%0d delivered=%0d",
             accepted_count, delivered_count);
    for (source = 0; source < N; source = source + 1) begin
      if (expected_head[source] != expected_tail[source])
        $fatal(1, "LOCKSTEP undrained source=%0d head=%0d tail=%0d",
               source, expected_head[source], expected_tail[source]);
    end

    $display("A7_K4_LOCKSTEP_PASS cycles=%0d accepted=%0d delivered=%0d drain=%0d",
             cycle_count, accepted_count, delivered_count, drain_cycles);
    $finish;
  end
endmodule
