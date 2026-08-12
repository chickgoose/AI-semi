`timescale 1ns/1ps

module a23_full_p6_replay_tb;
  localparam int MAX_EVENTS = 131072;
  localparam int DRAIN_TIMEOUT = 20000;

  logic ref_clk = 1'b0;
  logic sample_clk = 1'b0;
  logic rst_n = 1'b0;
  logic link_enable = 1'b1;
  logic [15:0] source_pending = '0;

  logic [15:0] source_accept;
  logic accept_valid;
  logic [1:0] accept_count;
  logic [3:0] accept_addr0;
  logic [3:0] accept_addr1;
  logic [1:0] retire_valid;
  logic [3:0] retire_addr0;
  logic [3:0] retire_addr1;
  logic protocol_error;
  logic drain_idle;

  integer trace_occurrence [0:MAX_EVENTS-1];
  integer trace_id [0:MAX_EVENTS-1];
  integer trace_source [0:MAX_EVENTS-1];
  integer trace_address [0:MAX_EVENTS-1];
  integer trace_deadline [0:MAX_EVENTS-1];

  integer record_source [0:MAX_EVENTS-1];
  integer record_trace_id [0:MAX_EVENTS-1];
  integer record_occurrence [0:MAX_EVENTS-1];
  integer record_accept [0:MAX_EVENTS-1];
  integer record_retire [0:MAX_EVENTS-1];
  integer record_deadline [0:MAX_EVENTS-1];
  integer record_state [0:MAX_EVENTS-1];
  integer pending_id [0:15];
  integer accepted_fifo [0:MAX_EVENTS-1];

  integer trace_count = 0;
  integer trace_cursor = 0;
  integer stim_cycles = 0;
  integer generated_count = 0;
  integer overrun_count = 0;
  integer accepted_count = 0;
  integer retired_count = 0;
  integer accepted_head = 0;
  integer accepted_tail = 0;
  integer global_cycle = 0;
  integer window_retired = 0;
  integer reset_test = 0;
  integer post_reset_guard = 0;
  logic measurement_active = 1'b0;

  string owner_name;
  string trace_name;
  string trace_file;
  string event_output;
  string summary_output;
  string mutation_name;

  always #8 ref_clk = ~ref_clk;
  initial begin
    #4;
    forever #8 sample_clk = ~sample_clk;
  end

`ifdef A23_OWNER_A2
  a23_a2_p6_observer_wrapper observed (
`elsif A23_OWNER_A3
  a23_a3_p6_observer_wrapper observed (
`elsif A23_OWNER_A4
  a23_a4_p6_observer_wrapper observed (
`else
  A23_OWNER_DEFINE_REQUIRED observed (
`endif
    .ref_clk_i(ref_clk), .sample_clk_i(sample_clk), .rst_n,
    .link_enable_i(link_enable), .source_pending_i(source_pending),
    .source_accept_o(source_accept), .accept_valid_o(accept_valid),
    .accept_count_o(accept_count), .accept_addr0_o(accept_addr0),
    .accept_addr1_o(accept_addr1), .retire_valid_o(retire_valid),
    .retire_addr0_o(retire_addr0), .retire_addr1_o(retire_addr1),
    .protocol_error_o(protocol_error), .drain_idle_o(drain_idle)
  );

  function automatic integer popcount16(input logic [15:0] value);
    integer bit_index;
    begin
      popcount16 = 0;
      for (bit_index = 0; bit_index < 16; bit_index = bit_index + 1)
        popcount16 = popcount16 + value[bit_index];
    end
  endfunction

  function automatic integer pending_count();
    integer source_index;
    begin
      pending_count = 0;
      for (source_index = 0; source_index < 16;
           source_index = source_index + 1)
        if (source_pending[source_index])
          pending_count = pending_count + 1;
    end
  endfunction

  task automatic fail(input string marker, input string reason);
    begin
      $display("%s owner=%s trace=%s mutation=%s cycle=%0d reason=%s",
               marker, owner_name, trace_name, mutation_name,
               global_cycle, reason);
      $fatal(1, "%s", reason);
    end
  endtask

  task automatic protocol_fail(input string reason);
    begin
      if (mutation_name == "microstep")
        fail("A23_REPLAY_MICROSTEP_FAIL", reason);
      else if ((mutation_name == "reset") || (post_reset_guard > 0))
        fail("A23_REPLAY_RESET_FAIL", reason);
      else
        fail("A23_REPLAY_PROTOCOL_FAIL", reason);
    end
  endtask

  task automatic order_fail(input string reason);
    begin
      if (mutation_name == "swap")
        fail("A23_REPLAY_SWAP_FAIL", reason);
      else if (mutation_name == "duplicate")
        fail("A23_REPLAY_DUP_FAIL", reason);
      else if (mutation_name == "drop")
        fail("A23_REPLAY_DROP_FAIL", reason);
      else
        fail("A23_REPLAY_ORDER_FAIL", reason);
    end
  endtask

  task automatic offer_record(
    input integer event_identity,
    input integer source_index,
    input integer trace_identity,
    input integer deadline_cycle
  );
    begin
      if ((event_identity < 0) || (event_identity >= MAX_EVENTS))
        fail("A23_REPLAY_CAPACITY_FAIL", "event record capacity exceeded");
      record_source[event_identity] = source_index;
      record_trace_id[event_identity] = trace_identity;
      record_occurrence[event_identity] = global_cycle;
      record_accept[event_identity] = -1;
      record_retire[event_identity] = -1;
      record_deadline[event_identity] = deadline_cycle;
      record_state[event_identity] = 0;
      generated_count = generated_count + 1;
      if (source_pending[source_index]) begin
        record_state[event_identity] = 1;
        overrun_count = overrun_count + 1;
      end else begin
        source_pending[source_index] = 1'b1;
        pending_id[source_index] = event_identity;
      end
    end
  endtask

  task automatic accept_one(input integer source_index);
    integer event_identity;
    begin
      if ((source_index < 0) || (source_index >= 16))
        fail("A23_REPLAY_ACCEPT_FAIL", "accepted source is outside N16");
      if (!source_pending[source_index] ||
          (pending_id[source_index] < 0))
        fail("A23_REPLAY_PHANTOM_FAIL",
             "atomic commit named a source without a pending occurrence");
      event_identity = pending_id[source_index];
      if (record_state[event_identity] != 0)
        fail("A23_REPLAY_DUP_FAIL", "occurrence accepted more than once");
      record_accept[event_identity] = global_cycle;
      record_state[event_identity] = 2;
      accepted_fifo[accepted_tail] = event_identity;
      accepted_tail = accepted_tail + 1;
      accepted_count = accepted_count + 1;
      // Match the actual common TB edge semantics: the DUT and scoreboard
      // observe the committed pending level, then the one-entry source latch
      // clears with a nonblocking update after that indexed edge.
      source_pending[source_index] <= 1'b0;
      pending_id[source_index] = -1;
    end
  endtask

  task automatic retire_one(input integer source_index);
    integer event_identity;
    begin
      if (accepted_head >= accepted_tail)
        if (mutation_name == "reset")
          fail("A23_REPLAY_RESET_FAIL",
               "P6 retired a phantom record after reset");
        else
          fail("A23_REPLAY_PHANTOM_FAIL",
               "P6 retired without a globally accepted occurrence");
      event_identity = accepted_fifo[accepted_head];
      if (record_state[event_identity] != 2)
        fail("A23_REPLAY_DUP_FAIL", "P6 retired an occurrence twice");
      if (record_source[event_identity] != source_index)
        order_fail($sformatf(
          "P6 retirement differs from global atomic accept order expected=%0d actual=%0d event=%0d head=%0d tail=%0d",
          record_source[event_identity], source_index, event_identity,
          accepted_head, accepted_tail));
      record_retire[event_identity] = global_cycle;
      record_state[event_identity] = 3;
      accepted_head = accepted_head + 1;
      retired_count = retired_count + 1;
      if (measurement_active)
        window_retired = window_retired + 1;
    end
  endtask

  always @(posedge ref_clk) begin
    if (!rst_n) begin
      #1;
      if (accept_valid || (source_accept != '0) ||
          (retire_valid != 2'b00) || protocol_error)
        fail("A23_REPLAY_RESET_FAIL", "visible activity escaped reset");
    end else begin
      if (accept_valid) begin
        if ((accept_count != 2'd1) && (accept_count != 2'd2))
          fail("A23_REPLAY_ACCEPT_FAIL", "commit count is outside one or two");
        if (popcount16(source_accept) != accept_count)
          fail("A23_REPLAY_ATOMIC_FAIL",
               "commit count and commit-derived source bitmap differ");
        if (!source_accept[accept_addr0])
          fail("A23_REPLAY_ATOMIC_FAIL", "lane zero missing from acceptance");
        if ((accept_count == 2'd2) &&
            (!source_accept[accept_addr1] ||
             (accept_addr0 == accept_addr1)))
          fail("A23_REPLAY_DUP_FAIL", "invalid duplicate atomic pair");
        accept_one(accept_addr0);
        if (accept_count == 2'd2)
          accept_one(accept_addr1);
      end else if ((accept_count != 0) || (source_accept != '0)) begin
        fail("A23_REPLAY_ATOMIC_FAIL",
             "accept count or bitmap was live without atomic commit");
      end

      // Sample the registered retire interface in the active region, exactly
      // as a synchronous common consumer does.  Sampling after #1 would see
      // the observer's just-updated NBA value and charge retirement one
      // indexed reference cycle too early.
      if (retire_valid != 2'b00) begin
        if ((retire_valid != 2'b01) && (retire_valid != 2'b11))
          fail("A23_REPLAY_RETIRE_SHAPE_FAIL",
               "P6 retire valid is not an atomic singleton or pair");
        if ((retire_valid == 2'b11) && (retire_addr0 == retire_addr1))
          order_fail("P6 duplicated an address within one atomic record");
        retire_one(retire_addr0);
        if (retire_valid == 2'b11)
          retire_one(retire_addr1);
      end

      // Protocol error is combinational across the current public seam and
      // may include state updated on this edge, so inspect it after NBA settle.
      #1;
      if (protocol_error)
        protocol_fail("actual integrated P6 top reported a protocol error");
      if (post_reset_guard > 0)
        post_reset_guard = post_reset_guard - 1;
      // Common/frozen occurrence semantics label the work observed at this
      // edge with the current indexed cycle, then advance the cycle counter.
      // Incrementing before acceptance would add a false cycle to every
      // occurrence-to-accept latency.
      global_cycle = global_cycle + 1;
    end
  end

  task automatic load_trace();
    integer trace_fd;
    integer scanned;
    integer version;
    integer source_count;
    integer load_milli;
    integer sink_mode;
    integer sink_arg0;
    integer sink_arg1;
    string seed_name;
    integer index;
    begin
      trace_fd = $fopen(trace_file, "r");
      if (trace_fd == 0)
        fail("A23_REPLAY_TRACE_FAIL", "cannot open prepared trace");
      scanned = $fscanf(trace_fd, "%d %d %d %d %d %d %d %d %s\n",
        version, trace_count, stim_cycles, source_count, load_milli,
        sink_mode, sink_arg0, sink_arg1, seed_name);
      if ((scanned != 9) || (version != 4) || (source_count != 16) ||
          (trace_count < 0) || (trace_count > MAX_EVENTS))
        fail("A23_REPLAY_TRACE_FAIL", "invalid generator-v4 trace header");
      for (index = 0; index < trace_count; index = index + 1) begin
        scanned = $fscanf(trace_fd, "%d %d %d %d %d\n",
          trace_occurrence[index], trace_id[index], trace_source[index],
          trace_address[index], trace_deadline[index]);
        if ((scanned != 5) || (trace_id[index] != index) ||
            (trace_source[index] < 0) || (trace_source[index] >= 16) ||
            (trace_address[index] != trace_source[index]) ||
            (trace_occurrence[index] < 0) ||
            (trace_occurrence[index] >= stim_cycles) ||
            ((index > 0) &&
             (trace_occurrence[index] < trace_occurrence[index-1])))
          fail("A23_REPLAY_TRACE_FAIL", "invalid generator-v4 event row");
      end
      $fclose(trace_fd);
    end
  endtask

  task automatic wait_drained();
    integer timeout;
    begin
      timeout = 0;
      while (!(drain_idle && (pending_count() == 0) &&
               (accepted_head == accepted_tail))) begin
        @(posedge ref_clk);
        #2;
        timeout = timeout + 1;
        // The actual transport can truthfully drain after a broken RTL path
        // drops an already accepted event.  Detect that terminal mismatch
        // here instead of waiting for the generic timeout.
        if (drain_idle && (pending_count() == 0) &&
            (accepted_head != accepted_tail)) begin
          if (mutation_name == "drop")
            fail("A23_REPLAY_DROP_FAIL",
                 "P6 drained with an accepted event missing");
          else
            fail("A23_REPLAY_CONSERVATION_FAIL",
                 "P6 drained before all accepted events retired");
        end
        if (timeout > DRAIN_TIMEOUT)
          fail("A23_REPLAY_DRAIN_FAIL", "actual integrated top did not drain");
      end
      repeat (4) begin
        @(posedge ref_clk);
        #2;
        if (retire_valid != 0)
          fail("A23_REPLAY_DUP_FAIL", "retirement appeared after global drain");
      end
    end
  endtask

  task automatic write_outputs();
    integer event_fd;
    integer summary_fd;
    integer index;
    string state_name;
    begin
      event_fd = $fopen(event_output, "w");
      if (event_fd == 0)
        fail("A23_REPLAY_OUTPUT_FAIL", "cannot open event output");
      $fdisplay(event_fd,
        "owner,trace,tb_only_event_id,logical_source,occurrence_cycle,accept_cycle,retire_cycle,deadline_cycle,event_state");
      for (index = 0; index < generated_count; index = index + 1) begin
        case (record_state[index])
          1: state_name = "source_overrun";
          2: state_name = "accepted";
          3: state_name = "retired";
          default: state_name = "pending";
        endcase
        $fdisplay(event_fd, "%s,%s,%0d,%0d,%0d,%0d,%0d,%0d,%s",
          owner_name, trace_name, record_trace_id[index], record_source[index],
          record_occurrence[index], record_accept[index], record_retire[index],
          record_deadline[index], state_name);
      end
      $fclose(event_fd);

      summary_fd = $fopen(summary_output, "w");
      if (summary_fd == 0)
        fail("A23_REPLAY_OUTPUT_FAIL", "cannot open summary output");
      $fdisplay(summary_fd,
        "owner,trace,generated,source_overrun,accepted,retired,fixed_window_retired,fixed_window_cycles,observation_cycles,reset_test");
      $fdisplay(summary_fd, "%s,%s,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",
        owner_name, trace_name, generated_count, overrun_count,
        accepted_count, retired_count, window_retired, stim_cycles,
        global_cycle, reset_test);
      $fclose(summary_fd);
    end
  endtask

  task automatic check_conservation();
    integer index;
    begin
      if (generated_count != (overrun_count + accepted_count))
        fail("A23_REPLAY_CONSERVATION_FAIL",
             "generated is not source-overrun plus accepted");
      if ((accepted_count != retired_count) ||
          (accepted_head != accepted_tail)) begin
        if (mutation_name == "drop")
          fail("A23_REPLAY_DROP_FAIL", "accepted event was dropped by P6");
        else
          fail("A23_REPLAY_CONSERVATION_FAIL",
               "actual accepted and globally retired totals differ");
      end
      if (pending_count() != 0)
        fail("A23_REPLAY_CONSERVATION_FAIL", "pending occurrences remain");
      for (index = 0; index < generated_count; index = index + 1)
        if ((record_state[index] != 1) && (record_state[index] != 3))
          fail("A23_REPLAY_CONSERVATION_FAIL",
               "occurrence lacks terminal overrun or retire classification");
    end
  endtask

  task automatic run_trace();
    integer local_cycle;
    begin
      load_trace();
      measurement_active = 1'b1;
      for (local_cycle = 0; local_cycle < stim_cycles;
           local_cycle = local_cycle + 1) begin
        @(negedge ref_clk);
        while ((trace_cursor < trace_count) &&
               (trace_occurrence[trace_cursor] == local_cycle)) begin
          offer_record(trace_cursor, trace_source[trace_cursor],
                       trace_id[trace_cursor], trace_deadline[trace_cursor]);
          trace_cursor = trace_cursor + 1;
        end
      end
      @(negedge ref_clk);
      measurement_active = 1'b0;
      wait_drained();
      check_conservation();
      write_outputs();
      $display("A23_REPLAY_TRACE_PASS owner=%s trace=%s generated=%0d overrun=%0d accepted=%0d retired=%0d fixed_window=%0d/%0d",
        owner_name, trace_name, generated_count, overrun_count,
        accepted_count, retired_count, window_retired, stim_cycles);
    end
  endtask

  task automatic run_reset();
    begin
      trace_name = "basic_reset_drain";
      stim_cycles = 0;
      @(negedge ref_clk);
      offer_record(0, 0, 0, global_cycle + 64);
      offer_record(1, 1, 1, global_cycle + 64);
      offer_record(2, 2, 2, global_cycle + 64);
      offer_record(3, 3, 3, global_cycle + 64);
      wait_drained();

      @(negedge ref_clk);
      rst_n = 1'b0;
      repeat (3) @(posedge ref_clk);
      @(negedge ref_clk);
      rst_n = 1'b1;
      post_reset_guard = 4;
      repeat (4) @(posedge ref_clk);

      @(negedge ref_clk);
      offer_record(4, 12, 4, global_cycle + 64);
      offer_record(5, 13, 5, global_cycle + 64);
      offer_record(6, 14, 6, global_cycle + 64);
      offer_record(7, 15, 7, global_cycle + 64);
      wait_drained();
      check_conservation();
      write_outputs();
      $display("A23_REPLAY_RESET_PASS owner=%s accepted=%0d retired=%0d",
               owner_name, accepted_count, retired_count);
    end
  endtask

  initial begin
    integer source_index;
    if (!$value$plusargs("OWNER=%s", owner_name))
      owner_name = "unspecified";
    if (!$value$plusargs("TRACE_NAME=%s", trace_name))
      trace_name = "unspecified";
    if (!$value$plusargs("TRACE_FILE=%s", trace_file))
      trace_file = "";
    if (!$value$plusargs("EVENT_OUTPUT=%s", event_output))
      event_output = "a23-events.csv";
    if (!$value$plusargs("SUMMARY_OUTPUT=%s", summary_output))
      summary_output = "a23-summary.csv";
    if (!$value$plusargs("MUTATION=%s", mutation_name))
      mutation_name = "none";
    reset_test = $test$plusargs("RESET_TEST");

    for (source_index = 0; source_index < 16;
         source_index = source_index + 1)
      pending_id[source_index] = -1;

    // Live input levels during reset must not create an accepted occurrence.
    source_pending = 16'hffff;
    repeat (4) @(posedge ref_clk);
    @(negedge ref_clk);
    source_pending = '0;
    rst_n = 1'b1;

    if (reset_test != 0)
      run_reset();
    else
      run_trace();

    $display("A23_REPLAY_ALL_PASS owner=%s trace=%s reset=%0d",
             owner_name, trace_name, reset_test);
    $finish;
  end
endmodule
