`timescale 1ns/1ps

module a23_synthetic_v2_ordinal_tb;
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
  integer trace_source [0:MAX_EVENTS-1];
  integer trace_deadline [0:MAX_EVENTS-1];
  integer record_source [0:MAX_EVENTS-1];
  integer record_occurrence [0:MAX_EVENTS-1];
  integer record_accept_cycle [0:MAX_EVENTS-1];
  integer record_accept_ordinal [0:MAX_EVENTS-1];
  integer record_retire_cycle [0:MAX_EVENTS-1];
  integer record_retire_ordinal [0:MAX_EVENTS-1];
  integer record_state [0:MAX_EVENTS-1];
  integer pending_id [0:15];
  integer accepted_fifo [0:MAX_EVENTS-1];

  integer trace_count = 0;
  integer trace_cursor = 0;
  integer stim_cycles = 0;
  integer generated_count = 0;
  integer accepted_head = 0;
  integer accepted_tail = 0;
  integer accept_ordinal_next = 0;
  integer retire_ordinal_next = 0;
  integer global_cycle = 0;

  string owner_name;
  string trace_name;
  string trace_file;
  string ordinal_output;

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

  task automatic fail(input string reason);
    begin
      $display("A23_SYNTHETIC_V2_ORDINAL_FAIL owner=%s trace=%s cycle=%0d reason=%s",
               owner_name, trace_name, global_cycle, reason);
      $fatal(1, "%s", reason);
    end
  endtask

  task automatic offer_record(input integer event_identity, input integer source_index);
    begin
      if ((event_identity < 0) || (event_identity >= MAX_EVENTS))
        fail("event record capacity exceeded");
      record_source[event_identity] = source_index;
      record_occurrence[event_identity] = global_cycle;
      record_accept_cycle[event_identity] = -1;
      record_accept_ordinal[event_identity] = -1;
      record_retire_cycle[event_identity] = -1;
      record_retire_ordinal[event_identity] = -1;
      record_state[event_identity] = 0;
      generated_count = generated_count + 1;
      if (source_pending[source_index]) begin
        record_state[event_identity] = 1;
      end else begin
        source_pending[source_index] = 1'b1;
        pending_id[source_index] = event_identity;
      end
    end
  endtask

  task automatic accept_one(input integer source_index);
    integer event_identity;
    begin
      if ((source_index < 0) || (source_index >= 16) ||
          !source_pending[source_index] || (pending_id[source_index] < 0))
        fail("acceptance has no pending occurrence");
      event_identity = pending_id[source_index];
      if (record_state[event_identity] != 0)
        fail("occurrence accepted more than once");
      record_accept_cycle[event_identity] = global_cycle;
      record_accept_ordinal[event_identity] = accept_ordinal_next;
      accept_ordinal_next = accept_ordinal_next + 1;
      record_state[event_identity] = 2;
      accepted_fifo[accepted_tail] = event_identity;
      accepted_tail = accepted_tail + 1;
      source_pending[source_index] <= 1'b0;
      pending_id[source_index] = -1;
    end
  endtask

  task automatic retire_one(input integer source_index);
    integer event_identity;
    begin
      if (accepted_head >= accepted_tail)
        fail("retirement has no accepted occurrence");
      event_identity = accepted_fifo[accepted_head];
      if ((record_state[event_identity] != 2) ||
          (record_source[event_identity] != source_index))
        fail("global retirement identity/order differs");
      record_retire_cycle[event_identity] = global_cycle;
      record_retire_ordinal[event_identity] = retire_ordinal_next;
      retire_ordinal_next = retire_ordinal_next + 1;
      record_state[event_identity] = 3;
      accepted_head = accepted_head + 1;
    end
  endtask

  always @(posedge clk) begin
    if (!rst_n) begin
      #1;
      if (accept_valid || (source_accept != '0) || (accept_count != 0) ||
          (retire_valid != 0) || protocol_error)
        fail("visible endpoint activity escaped reset");
    end else begin
      if (accept_valid) begin
        if ((accept_count != 2'd1) && (accept_count != 2'd2))
          fail("accept count is outside one or two");
        if (popcount16(source_accept) != accept_count)
          fail("accept bitmap/count differ");
        if (!source_accept[accept_addr0])
          fail("lane zero is absent from accept bitmap");
        if ((accept_count == 2'd1) && (accept_addr1 != 4'd0))
          fail("singleton unused address is noncanonical");
        if ((accept_count == 2'd2) &&
            (!source_accept[accept_addr1] || (accept_addr0 == accept_addr1)))
          fail("accepted pair shape differs");
        accept_one(accept_addr0);
        if (accept_count == 2'd2)
          accept_one(accept_addr1);
      end else if ((accept_count != 0) || (source_accept != '0)) begin
        fail("accept payload is live without valid");
      end

      if (retire_valid != 0) begin
        if ((retire_valid != 2'b01) && (retire_valid != 2'b11))
          fail("retire valid is not an ordered prefix");
        if ((retire_valid == 2'b11) && (retire_addr0 == retire_addr1))
          fail("retire pair duplicates an address");
        retire_one(retire_addr0);
        if (retire_valid == 2'b11)
          retire_one(retire_addr1);
      end
      #1;
      if (protocol_error)
        fail("actual endpoint asserted protocol_error");
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
    integer trace_id;
    integer trace_address;
    string seed_name;
    integer index;
    begin
      trace_fd = $fopen(trace_file, "r");
      if (trace_fd == 0)
        fail("cannot open prepared trace");
      scanned = $fscanf(trace_fd, "%d %d %d %d %d %d %d %d %s\n",
        version, trace_count, stim_cycles, source_count, load_milli,
        sink_mode, sink_arg0, sink_arg1, seed_name);
      if ((scanned != 9) || (version != 4) || (source_count != 16) ||
          (trace_count < 0) || (trace_count > MAX_EVENTS))
        fail("invalid prepared trace header");
      for (index = 0; index < trace_count; index = index + 1) begin
        scanned = $fscanf(trace_fd, "%d %d %d %d %d\n",
          trace_occurrence[index], trace_id, trace_source[index],
          trace_address, trace_deadline[index]);
        if ((scanned != 5) || (trace_id != index) ||
            (trace_source[index] < 0) || (trace_source[index] >= 16) ||
            (trace_address != trace_source[index]) ||
            (trace_occurrence[index] < 0) ||
            (trace_occurrence[index] >= stim_cycles) ||
            ((index > 0) && (trace_occurrence[index] < trace_occurrence[index-1])))
          fail("invalid prepared trace row");
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
        if (timeout > DRAIN_TIMEOUT)
          fail("actual endpoint did not drain");
      end
      repeat (4) begin
        @(posedge clk);
        #2;
        if ((retire_valid != 0) || accept_valid || (source_accept != 0))
          fail("visible activity appeared after drain");
      end
    end
  endtask

  task automatic write_ordinals();
    integer output_fd;
    integer index;
    string state_name;
    begin
      output_fd = $fopen(ordinal_output, "w");
      if (output_fd == 0)
        fail("cannot open ordinal output");
      $fdisplay(output_fd,
        "owner,trace,tb_event_id,logical_source,occurrence_cycle,accept_cycle,accept_ordinal,retire_cycle,retire_ordinal,event_state");
      for (index = 0; index < generated_count; index = index + 1) begin
        case (record_state[index])
          1: state_name = "source_overrun";
          3: state_name = "retired";
          default: state_name = "nonterminal";
        endcase
        $fdisplay(output_fd, "%s,%s,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%s",
          owner_name, trace_name, index, record_source[index],
          record_occurrence[index], record_accept_cycle[index],
          record_accept_ordinal[index], record_retire_cycle[index],
          record_retire_ordinal[index], state_name);
      end
      $fclose(output_fd);
    end
  endtask

  initial begin
    integer source_index;
    integer local_cycle;
    if (!$value$plusargs("OWNER=%s", owner_name)) owner_name = "unspecified";
    if (!$value$plusargs("TRACE_NAME=%s", trace_name)) trace_name = "unspecified";
    if (!$value$plusargs("TRACE_FILE=%s", trace_file)) trace_file = "";
    if (!$value$plusargs("ORDINAL_OUTPUT=%s", ordinal_output)) ordinal_output = "ordinals.csv";
    for (source_index = 0; source_index < 16; source_index = source_index + 1)
      pending_id[source_index] = -1;

    source_pending = 16'hffff;
    repeat (4) @(posedge clk);
    @(negedge clk);
    source_pending = '0;
    rst_n = 1'b1;
    load_trace();

    for (local_cycle = 0; local_cycle < stim_cycles; local_cycle = local_cycle + 1) begin
      @(negedge clk);
      while ((trace_cursor < trace_count) &&
             (trace_occurrence[trace_cursor] == local_cycle)) begin
        offer_record(trace_cursor, trace_source[trace_cursor]);
        trace_cursor = trace_cursor + 1;
      end
    end
    @(negedge clk);
    wait_drained();
    if ((accept_ordinal_next != retire_ordinal_next) ||
        (accepted_head != accepted_tail))
      fail("accepted/retired ordinal conservation differs");
    write_ordinals();
    $display("A23_SYNTHETIC_V2_ORDINAL_PASS owner=%s trace=%s generated=%0d accepted=%0d retired=%0d",
      owner_name, trace_name, generated_count, accept_ordinal_next,
      retire_ordinal_next);
    $finish;
  end
endmodule
