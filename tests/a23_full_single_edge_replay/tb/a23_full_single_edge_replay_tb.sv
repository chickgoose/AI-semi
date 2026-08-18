`timescale 1ns/1ps

module a23_full_single_edge_replay_tb;
  localparam int MAX_EVENTS = 131072;
  localparam int DRAIN_TIMEOUT = 20000;

  logic clk = 1'b0;
  logic rst_n = 1'b0;
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
  integer source_overrun_count = 0;
  integer accepted_count = 0;
  integer retired_count = 0;
  integer accepted_head = 0;
  integer accepted_tail = 0;
  integer global_cycle = 0;
  integer fixed_window_retired = 0;
  integer count2_commits = 0;
  integer reset_test = 0;
  logic measurement_active = 1'b0;

  string owner_name;
  string trace_name;
  string trace_file;
  string event_output;
  string summary_output;
  string mutation_name;

  always #5 clk = ~clk;

`ifdef A23_SE_OWNER_A2
  a23_a2_single_edge_observer_wrapper observed (
`elsif A23_SE_OWNER_A3
  a23_a3_single_edge_observer_wrapper observed (
`else
  A23_SE_OWNER_DEFINE_REQUIRED observed (
`endif
    .clk_i(clk), .rst_n_i(rst_n), .source_pending_i(source_pending),
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
      for (source_index = 0; source_index < 16; source_index = source_index + 1)
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

  task automatic offer_record(
    input integer event_identity,
    input integer source_index,
    input integer trace_identity,
    input integer deadline_cycle
  );
    begin
      if ((event_identity < 0) || (event_identity >= MAX_EVENTS))
        fail("A23_SE_CAPACITY_FAIL", "event record capacity exceeded");
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
        source_overrun_count = source_overrun_count + 1;
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
        fail("A23_SE_ACCEPT_FAIL", "accepted source is outside N16");
      if (!source_pending[source_index] || (pending_id[source_index] < 0))
        fail("A23_SE_PHANTOM_FAIL", "accepted source has no pending occurrence");
      event_identity = pending_id[source_index];
      if (record_state[event_identity] != 0)
        fail("A23_SE_DUPLICATE_FAIL", "occurrence accepted more than once");
      record_accept[event_identity] = global_cycle;
      record_state[event_identity] = 2;
      accepted_fifo[accepted_tail] = event_identity;
      accepted_tail = accepted_tail + 1;
      accepted_count = accepted_count + 1;
      // Canonical source semantics: accept the level at this indexed edge,
      // then clear its one-entry latch after the edge with an NBA update.
      source_pending[source_index] <= 1'b0;
      pending_id[source_index] = -1;
    end
  endtask

  task automatic retire_one(input integer source_index);
    integer event_identity;
    begin
      if (accepted_head >= accepted_tail)
        fail("A23_SE_PHANTOM_FAIL", "retirement has no accepted occurrence");
      event_identity = accepted_fifo[accepted_head];
      if (record_state[event_identity] != 2)
        fail("A23_SE_DUPLICATE_FAIL", "accepted occurrence retired more than once");
      if (record_source[event_identity] != source_index)
        fail("A23_SE_REORDER_FAIL", $sformatf(
          "global retirement order mismatch expected=%0d actual=%0d event=%0d",
          record_source[event_identity], source_index, event_identity));
      record_retire[event_identity] = global_cycle;
      record_state[event_identity] = 3;
      accepted_head = accepted_head + 1;
      retired_count = retired_count + 1;
      if (measurement_active)
        fixed_window_retired = fixed_window_retired + 1;
    end
  endtask

  always @(posedge clk) begin
    if (!rst_n) begin
      #1;
      if (accept_valid || (source_accept != '0) || (accept_count != 0) ||
          (retire_valid != 0) || protocol_error)
        fail("A23_SE_RESET_ESCAPE_FAIL", "visible endpoint activity escaped reset");
    end else begin
      if (accept_valid) begin
        if ((accept_count != 2'd1) && (accept_count != 2'd2))
          fail("A23_SE_ACCEPT_FAIL", "accept count is outside one or two");
        if (popcount16(source_accept) != accept_count)
          fail("A23_SE_ATOMIC_FAIL", "accept bitmap and count differ");
        if (!source_accept[accept_addr0])
          fail("A23_SE_ATOMIC_FAIL", "lane zero is absent from accept bitmap");
        if ((accept_count == 2'd2) &&
            (!source_accept[accept_addr1] || (accept_addr0 == accept_addr1)))
          fail("A23_SE_DUPLICATE_FAIL", "invalid duplicate accepted pair");
        accept_one(accept_addr0);
        if (accept_count == 2'd2) begin
          count2_commits = count2_commits + 1;
          accept_one(accept_addr1);
        end
      end else if ((accept_count != 0) || (source_accept != '0)) begin
        fail("A23_SE_ATOMIC_FAIL", "accept payload is live without accept_valid");
      end

      if (retire_valid != 0) begin
        if ((retire_valid != 2'b01) && (retire_valid != 2'b11))
          fail("A23_SE_RETIRE_SHAPE_FAIL", "retire valid is not ordered prefix 01/11");
        if ((retire_valid == 2'b11) && (retire_addr0 == retire_addr1))
          fail("A23_SE_DUPLICATE_FAIL", "retire pair duplicated an address");
        retire_one(retire_addr0);
        if (retire_valid == 2'b11)
          retire_one(retire_addr1);
      end

      #1;
      if (protocol_error)
        fail("A23_SE_PROTOCOL_FAIL", "actual endpoint asserted protocol_error");
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
        fail("A23_SE_TRACE_FAIL", "cannot open prepared trace");
      scanned = $fscanf(trace_fd, "%d %d %d %d %d %d %d %d %s\n",
        version, trace_count, stim_cycles, source_count, load_milli,
        sink_mode, sink_arg0, sink_arg1, seed_name);
      if ((scanned != 9) || (version != 4) || (source_count != 16) ||
          (trace_count < 0) || (trace_count > MAX_EVENTS))
        fail("A23_SE_TRACE_FAIL", "invalid generator-v4 trace header");
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
          fail("A23_SE_TRACE_FAIL", "invalid generator-v4 event row");
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
        @(posedge clk);
        #2;
        timeout = timeout + 1;
        if (drain_idle && (pending_count() == 0) &&
            (accepted_head != accepted_tail)) begin
          if (mutation_name == "drop")
            fail("A23_SE_DROP_FAIL", "endpoint drained with accepted work missing");
          else
            fail("A23_SE_CONSERVATION_FAIL", "drain preceded accepted retirement");
        end
        if (timeout > DRAIN_TIMEOUT)
          fail("A23_SE_DRAIN_FAIL", "actual endpoint did not drain");
      end
      repeat (4) begin
        @(posedge clk);
        #2;
        if (retire_valid != 0)
          fail("A23_SE_DUPLICATE_FAIL", "retirement appeared after global drain");
        if (accept_valid || (source_accept != 0))
          fail("A23_SE_PHANTOM_FAIL", "acceptance appeared after global drain");
      end
    end
  endtask

  task automatic check_conservation();
    integer index;
    begin
      if (generated_count != source_overrun_count + accepted_count)
        fail("A23_SE_CONSERVATION_FAIL", "generated != source_overrun + accepted");
      if ((accepted_count != retired_count) ||
          (accepted_head != accepted_tail))
        fail("A23_SE_CONSERVATION_FAIL", "accepted != retired after drain");
      if (pending_count() != 0)
        fail("A23_SE_CONSERVATION_FAIL", "pending source occurrences remain");
      for (index = 0; index < generated_count; index = index + 1)
        if ((record_state[index] != 1) && (record_state[index] != 3))
          fail("A23_SE_CONSERVATION_FAIL", "event lacks terminal classification");
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
        fail("A23_SE_OUTPUT_FAIL", "cannot open event output");
      $fdisplay(event_fd,
        "owner,trace,tb_event_id,logical_source,occurrence_cycle,accept_cycle,retire_cycle,deadline_cycle,event_state");
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
        fail("A23_SE_OUTPUT_FAIL", "cannot open summary output");
      $fdisplay(summary_fd,
        "owner,trace,generated,source_overrun,accepted,retired,fixed_window_retired,fixed_window_cycles,observation_cycles,count2_commits,reset_test");
      $fdisplay(summary_fd, "%s,%s,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",
        owner_name, trace_name, generated_count, source_overrun_count,
        accepted_count, retired_count, fixed_window_retired, stim_cycles,
        global_cycle, count2_commits, reset_test);
      $fclose(summary_fd);
    end
  endtask

  task automatic run_full_trace();
    integer local_cycle;
    begin
      load_trace();
      measurement_active = 1'b1;
      for (local_cycle = 0; local_cycle < stim_cycles;
           local_cycle = local_cycle + 1) begin
        @(negedge clk);
        while ((trace_cursor < trace_count) &&
               (trace_occurrence[trace_cursor] == local_cycle)) begin
          offer_record(trace_cursor, trace_source[trace_cursor],
                       trace_id[trace_cursor], trace_deadline[trace_cursor]);
          trace_cursor = trace_cursor + 1;
        end
      end
      @(negedge clk);
      measurement_active = 1'b0;
      wait_drained();
      check_conservation();
      write_outputs();
    end
  endtask

  task automatic run_pair();
    begin
      stim_cycles = 1;
      measurement_active = 1'b1;
      @(negedge clk);
      offer_record(0, 0, 0, global_cycle + 64);
      offer_record(1, 1, 1, global_cycle + 64);
      @(negedge clk);
      measurement_active = 1'b0;
      wait_drained();
      if (count2_commits == 0)
        fail("A23_SE_ACTIVATION_FAIL", "directed case did not commit a count-two bundle");
      check_conservation();
      write_outputs();
    end
  endtask

  task automatic run_reset();
    begin
      trace_name = "reset_drain_epochs";
      stim_cycles = 0;
      @(negedge clk);
      offer_record(0, 0, 0, global_cycle + 64);
      offer_record(1, 1, 1, global_cycle + 64);
      wait_drained();

      @(negedge clk);
      source_pending = 16'hffff;
      rst_n = 1'b0;
      repeat (3) @(posedge clk);
      @(negedge clk);
      source_pending = '0;
      rst_n = 1'b1;
      repeat (4) @(posedge clk);

      @(negedge clk);
      offer_record(2, 14, 2, global_cycle + 64);
      offer_record(3, 15, 3, global_cycle + 64);
      wait_drained();
      check_conservation();
      write_outputs();
    end
  endtask

  initial begin
    integer source_index;
    string mode_name;
    if (!$value$plusargs("OWNER=%s", owner_name)) owner_name = "unspecified";
    if (!$value$plusargs("TRACE_NAME=%s", trace_name)) trace_name = "unspecified";
    if (!$value$plusargs("TRACE_FILE=%s", trace_file)) trace_file = "";
    if (!$value$plusargs("EVENT_OUTPUT=%s", event_output)) event_output = "events.csv";
    if (!$value$plusargs("SUMMARY_OUTPUT=%s", summary_output)) summary_output = "summary.csv";
    if (!$value$plusargs("MUTATION=%s", mutation_name)) mutation_name = "none";
    if (!$value$plusargs("MODE=%s", mode_name)) mode_name = "full";
    reset_test = (mode_name == "reset");
    for (source_index = 0; source_index < 16; source_index = source_index + 1)
      pending_id[source_index] = -1;

    // Inputs are deliberately live during initial reset; they are not logical
    // occurrences and must not be accepted by the endpoint.
    source_pending = 16'hffff;
    repeat (4) @(posedge clk);
    @(negedge clk);
    source_pending = '0;
    rst_n = 1'b1;

    if (mode_name == "full")
      run_full_trace();
    else if (mode_name == "pair")
      run_pair();
    else if (mode_name == "reset")
      run_reset();
    else
      fail("A23_SE_MODE_FAIL", "unknown execution mode");

    $display("A23_SE_ACTUAL_RTL_PASS owner=%s trace=%s mode=%s generated=%0d source_overrun=%0d accepted=%0d retired=%0d",
      owner_name, trace_name, mode_name, generated_count,
      source_overrun_count, accepted_count, retired_count);
    $finish;
  end
endmodule
