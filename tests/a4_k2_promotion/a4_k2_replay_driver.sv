`timescale 1ns/1ps

module a4_k2_replay_driver;
  localparam integer SOURCE_COUNT = 16;
  localparam integer MAX_EVENTS = 65536;

  logic clk = 1'b0;
  always #5 clk = ~clk;

  logic rst;
  logic [15:0] source_valid;
  logic [31:0] source_event [16];
  wire [15:0] source_ready;
  wire [1:0] accept_valid;
  wire [3:0] accept_source [2];
  wire [31:0] accept_event [2];
  wire [1:0] retire_valid;
  logic [1:0] retire_ready;
  wire [3:0] retire_source [2];
  wire [31:0] retire_event [2];
  wire drain_idle;

  logic [15:0] pending_valid;
  logic [31:0] pending_event [16];
  integer pending_occurrence [16];
  integer accepted_id [MAX_EVENTS];
  integer accepted_source_log [MAX_EVENTS];
  integer accepted_cycle_log [MAX_EVENTS];

  integer vector_file;
  integer scan_count;
  integer version;
  integer total_vector_cycles;
  integer expected_generated;
  integer measurement_start;
  integer measurement_end;
  integer expected_measurement_generated;
  integer max_transport_latency;
  integer input_cycle;
  integer input_reset_n;
  integer input_ready;
  integer input_occurrence_mask;
  integer event_code [16];
  integer cycle;
  integer source;
  integer lane;
  integer generated_count;
  integer measured_generated_count;
  integer overrun_count;
  integer reset_aborted_count;
  integer accepted_count;
  integer retired_count;
  integer measured_retired_count;
  integer accept_head;
  integer accept_tail;
  integer latency;
  integer max_occurrence_accept_latency;
  integer max_accept_retire_latency;
  integer expected_mask;
  integer observed_accept_mask;
  integer observed_retire_mask;
  integer reset_cycle_count;
  integer mutate_time_shift;
  integer held_active;
  logic [1:0] held_valid;
  logic [3:0] held_source0;
  logic [3:0] held_source1;
  logic [31:0] held_event0;
  logic [31:0] held_event1;
  string vector_path;
  string run_name;
  string suite_name;

  assign source_valid = pending_valid;
  always @* begin
    for (source = 0; source < SOURCE_COUNT; source = source + 1)
      source_event[source] = pending_event[source];
  end

  a4_k2_transaction_boundary dut (
    .clk(clk), .rst(rst),
    .source_valid(source_valid), .source_event(source_event),
    .source_ready(source_ready),
    .accept_valid(accept_valid), .accept_source(accept_source),
    .accept_event(accept_event),
    .retire_valid(retire_valid), .retire_ready(retire_ready),
    .retire_source(retire_source), .retire_event(retire_event),
    .drain_idle(drain_idle)
  );

  task automatic fail(input string reason);
    begin
      $display("A4_K2_REPLAY_FAIL suite=%s run=%s cycle=%0d reason=%s",
               suite_name, run_name, cycle, reason);
      $fatal(1, "A4_K2_REPLAY_FAIL %s", reason);
    end
  endtask

  function automatic integer bit_count16(input logic [15:0] bits);
    integer index;
    begin
      bit_count16 = 0;
      for (index = 0; index < 16; index = index + 1)
        bit_count16 = bit_count16 + bits[index];
    end
  endfunction

  initial begin
    if (!$value$plusargs("VECTOR=%s", vector_path))
      fail("missing +VECTOR path");
    if (!$value$plusargs("RUN=%s", run_name))
      run_name = "unknown";
    if (!$value$plusargs("SUITE=%s", suite_name))
      suite_name = "unknown";
    if (!$value$plusargs("A4_MUTATE_TIME_SHIFT=%d", mutate_time_shift))
      mutate_time_shift = 0;

    vector_file = $fopen(vector_path, "r");
    if (vector_file == 0)
      fail("cannot open vector file");
    scan_count = $fscanf(vector_file, "%d %d %d %d %d %d %d\n",
                         version, total_vector_cycles, expected_generated,
                         measurement_start, measurement_end,
                         expected_measurement_generated, max_transport_latency);
    if (scan_count != 7 || version != 1)
      fail("malformed vector header");
    if (total_vector_cycles <= 0 || measurement_start < 0 ||
        measurement_end < measurement_start || measurement_end > total_vector_cycles)
      fail("invalid vector cycle/window cardinality");

    rst = 1'b1;
    retire_ready = 2'b00;
    pending_valid = '0;
    generated_count = 0;
    measured_generated_count = 0;
    overrun_count = 0;
    reset_aborted_count = 0;
    accepted_count = 0;
    retired_count = 0;
    measured_retired_count = 0;
    accept_head = 0;
    accept_tail = 0;
    max_occurrence_accept_latency = 0;
    max_accept_retire_latency = 0;
    reset_cycle_count = 0;
    held_active = 0;
    cycle = -1;
    for (source = 0; source < SOURCE_COUNT; source = source + 1) begin
      pending_event[source] = '0;
      pending_occurrence[source] = -1;
      event_code[source] = 0;
    end

    // This setup reset is outside the exported cycle namespace.  Vector cycle
    // zero therefore remains generator occurrence cycle zero.
    repeat (2) @(posedge clk);

    for (cycle = 0; cycle < total_vector_cycles; cycle = cycle + 1) begin
      @(negedge clk);
      scan_count = $fscanf(vector_file,
        "%d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d\n",
        input_cycle, input_reset_n, input_ready, input_occurrence_mask,
        event_code[0], event_code[1], event_code[2], event_code[3],
        event_code[4], event_code[5], event_code[6], event_code[7],
        event_code[8], event_code[9], event_code[10], event_code[11],
        event_code[12], event_code[13], event_code[14], event_code[15]);
      if (scan_count != 20)
        fail("malformed or truncated vector cycle");
      if (input_cycle != cycle + mutate_time_shift)
        fail("vector/index time shift detected");
      if ((input_reset_n != 0 && input_reset_n != 1) ||
          (input_ready != 0 && input_ready != 1))
        fail("non-Boolean vector control");

      expected_mask = 0;
      // Frozen common-TB order: occurrences are classified at this negedge
      // against the still-pending source latch.  The following posedge may
      // accept/retire the old record, but cannot retroactively rearm a new
      // same-source occurrence that was already classified as overrun.
      for (source = 0; source < SOURCE_COUNT; source = source + 1) begin
        if (event_code[source] < 0)
          fail("negative occurrence identity code");
        if (event_code[source] != 0)
          expected_mask = expected_mask | (1 << source);
      end
      if (expected_mask != input_occurrence_mask)
        fail("cycle occurrence mask does not match identities");

      rst = !input_reset_n;
      retire_ready = input_ready ? 2'b11 : 2'b00;
      if (!input_reset_n) begin
        reset_cycle_count = reset_cycle_count + 1;
        reset_aborted_count = reset_aborted_count + bit_count16(pending_valid);
        pending_valid = '0;
        held_active = 0;
      end

      for (source = 0; source < SOURCE_COUNT; source = source + 1) begin
        if (event_code[source] != 0) begin
          generated_count = generated_count + 1;
          if (cycle >= measurement_start && cycle < measurement_end)
            measured_generated_count = measured_generated_count + 1;
          if (!input_reset_n) begin
            reset_aborted_count = reset_aborted_count + 1;
          end else if (pending_valid[source]) begin
            overrun_count = overrun_count + 1;
          end else begin
            pending_valid[source] = 1'b1;
            pending_event[source] = event_code[source];
            pending_occurrence[source] = cycle;
          end
        end
      end

      @(posedge clk);
      if (rst) begin
        if (source_ready !== 16'b0 || accept_valid !== 2'b00 ||
            retire_valid !== 2'b00 || drain_idle !== 1'b0)
          fail("normalized boundary was not quiet during reset");
      end else begin
        if (accept_valid == 2'b10 || retire_valid == 2'b10)
          fail("ordered lane hole");
        if (accept_valid == 2'b11 && accept_source[0] == accept_source[1])
          fail("duplicate source in accepted K2 transaction");
        if (retire_valid == 2'b11 && retire_source[0] == retire_source[1])
          fail("duplicate source in retired K2 transaction");
        if ((source_ready & ~pending_valid) != 0)
          fail("source-ready accepted a non-pending occurrence");

        if (held_active != 0) begin
          if (retire_valid !== held_valid || retire_source[0] !== held_source0 ||
              retire_source[1] !== held_source1 || retire_event[0] !== held_event0 ||
              retire_event[1] !== held_event1)
            fail("stalled ordered transaction changed");
        end

        observed_accept_mask = 0;
        for (lane = 0; lane < 2; lane = lane + 1) begin
          if (accept_valid[lane]) begin
            source = accept_source[lane];
            if (!pending_valid[source])
              fail("phantom accepted source");
            if (accept_event[lane] !== pending_event[source])
              fail("accepted occurrence identity corruption");
            if ((observed_accept_mask & (1 << source)) != 0)
              fail("duplicate accepted source in one transaction");
            observed_accept_mask = observed_accept_mask | (1 << source);
            if (accept_tail >= MAX_EVENTS)
              fail("accepted event log overflow");
            accepted_id[accept_tail] = accept_event[lane];
            accepted_source_log[accept_tail] = source;
            accepted_cycle_log[accept_tail] = cycle;
            accept_tail = accept_tail + 1;
            accepted_count = accepted_count + 1;
            latency = cycle - pending_occurrence[source];
            if (latency < 0)
              fail("acceptance precedes occurrence");
            if (latency > max_occurrence_accept_latency)
              max_occurrence_accept_latency = latency;
          end
        end
        if (observed_accept_mask != source_ready)
          fail("ordered accepts and source-ready bitmap disagree");

        observed_retire_mask = 0;
        for (lane = 0; lane < 2; lane = lane + 1) begin
          if (retire_valid[lane] && retire_ready[lane]) begin
            source = retire_source[lane];
            if (accept_head >= accept_tail)
              fail("retirement without an accepted occurrence");
            if (retire_event[lane] !== accepted_id[accept_head] ||
                source != accepted_source_log[accept_head])
              fail("accepted/retired global order or identity mismatch");
            if ((observed_retire_mask & (1 << source)) != 0)
              fail("duplicate retired source in one transaction");
            observed_retire_mask = observed_retire_mask | (1 << source);
            latency = cycle - accepted_cycle_log[accept_head];
            if (latency < 0 || latency > max_transport_latency)
              fail("accepted-to-retired latency outside vector contract");
            if (latency > max_accept_retire_latency)
              max_accept_retire_latency = latency;
            accept_head = accept_head + 1;
            retired_count = retired_count + 1;
            if (cycle >= measurement_start && cycle < measurement_end)
              measured_retired_count = measured_retired_count + 1;
          end
        end
        if (observed_retire_mask != source_ready)
          fail("atomic accepted and retired source sets disagree");

        for (source = 0; source < SOURCE_COUNT; source = source + 1)
          if (source_ready[source])
            pending_valid[source] <= 1'b0;

        if (retire_valid != 0 && retire_ready == 0) begin
          held_active = 1;
          held_valid = retire_valid;
          held_source0 = retire_source[0];
          held_source1 = retire_source[1];
          held_event0 = retire_event[0];
          held_event1 = retire_event[1];
        end else begin
          held_active = 0;
        end
      end

    end

    @(negedge clk);
    rst = 1'b0;
    retire_ready = 2'b11;
    #1;
    if (pending_valid != 0 || accept_head != accept_tail || !drain_idle)
      fail("vector ended before complete drain");
    if (generated_count != expected_generated)
      fail("generated count differs from vector header");
    if (measured_generated_count != expected_measurement_generated)
      fail("measurement-window generated count differs from vector header");
    if (generated_count != overrun_count + reset_aborted_count + accepted_count)
      fail("generation conservation failed");
    if (accepted_count != retired_count)
      fail("accepted/retired conservation failed");
    if (max_accept_retire_latency > max_transport_latency)
      fail("transport latency maximum violated");
    if (!$feof(vector_file)) begin
      scan_count = $fscanf(vector_file, "%d", input_cycle);
      if (scan_count == 1)
        fail("extra vector cycles after declared cardinality");
    end
    $fclose(vector_file);
    $display("A4_K2_REPLAY_PASS suite=%s run=%s cycles=%0d generated=%0d overrun=%0d reset_aborted=%0d accepted=%0d retired=%0d measured_generated=%0d measured_retired=%0d max_occ_accept=%0d max_accept_retire=%0d reset_cycles=%0d",
             suite_name, run_name, total_vector_cycles, generated_count,
             overrun_count, reset_aborted_count, accepted_count, retired_count,
             measured_generated_count, measured_retired_count,
             max_occurrence_accept_latency, max_accept_retire_latency,
             reset_cycle_count);
    $finish;
  end
endmodule
