`timescale 1ns/1ps

module a4_fovea_a7_common_trace_tb;
  localparam integer MAX_EVENTS = 131072;
  localparam time HALF = 8ns;

  logic ref_clk_i = 1'b0;
  logic sample_clk_i = 1'b0;
  logic rst_n = 1'b0;
  logic [15:0] source_valid = '0;
  logic [15:0] source_ready;
  logic burst_clk_o;
  logic [1:0] burst_data_o;
  logic [3:0] retire_addr_o;
  logic retire_valid_o, drain_idle_o, protocol_fault_o;

  integer occurrence [0:MAX_EVENTS-1];
  integer trace_id [0:MAX_EVENTS-1];
  integer trace_source [0:MAX_EVENTS-1];
  integer trace_address [0:MAX_EVENTS-1];
  integer deadline [0:MAX_EVENTS-1];
  integer accept_cycle [0:MAX_EVENTS-1];
  integer delivery_cycle [0:MAX_EVENTS-1];
  integer event_state [0:MAX_EVENTS-1];
  integer pending_id [0:15];
  integer request_wait [0:15];
  integer delivered_by_source [0:15];
  integer accepted_order [0:MAX_EVENTS-1];
  integer generated, accepted, delivered, overrun, errors;
  integer accepted_head, accepted_tail, sim_cycle;
  integer event_cursor, stim_cycle, timeout, source, id;
  integer trace_fd, event_fd, summary_fd, scan_count;
  integer trace_version, trace_stim_cycles, trace_source_count;
  integer load_milli, sink_mode, sink_arg0, sink_arg1, trace_seed;
  integer e2e_sum, internal_sum, max_e2e, max_internal;
  integer max_request_wait;
  string trace_path, events_path, summary_path, trace_name;
  realtime reset_release_time;
  bit traffic_active;

  function automatic real service_fairness;
    integer fairness_source, service_sum, square_sum;
    begin
      service_sum = 0;
      square_sum = 0;
      for (fairness_source = 0; fairness_source < 16; fairness_source = fairness_source + 1) begin
        service_sum = service_sum + delivered_by_source[fairness_source];
        square_sum = square_sum +
          delivered_by_source[fairness_source] * delivered_by_source[fairness_source];
      end
      service_fairness = (square_sum != 0) ? real'(service_sum * service_sum) /
        real'(16 * square_sum) : 0.0;
    end
  endfunction

  // Reference-only arrays and accepted_order are scoreboard state, not DUT
  // storage.  The only ingress storage is the common one-pending/source latch.
  a7_weighted_fovea_ddr dut (
    .ref_clk_i, .sample_clk_i, .rst_n, .source_valid, .source_ready,
    .burst_clk_o, .burst_data_o, .retire_addr_o, .retire_valid_o,
    .drain_idle_o, .protocol_fault_o
  );

  always #HALF ref_clk_i = ~ref_clk_i;
  initial begin
    #(HALF + 4ns) sample_clk_i = 1'b1;
    forever #HALF sample_clk_i = ~sample_clk_i;
  end

  always @(posedge ref_clk_i) begin
    if (rst_n && traffic_active) begin
      if (($realtime - reset_release_time) == 4ns && sim_cycle == 0)
        $display("A4_COMMON_TRACE_PHASE_PASS fall_to_ref=4ns");
      if (!$onehot0(source_ready)) begin
        $error("source_ready is not onehot0: %h", source_ready);
        errors = errors + 1;
      end
      for (source = 0; source < 16; source = source + 1) begin
        if (source_valid[source] && !source_ready[source]) begin
          request_wait[source] = request_wait[source] + 1;
          if (request_wait[source] > max_request_wait)
            max_request_wait = request_wait[source];
        end else begin
          request_wait[source] = 0;
        end
        if (source_ready[source]) begin
          if (!source_valid[source] || pending_id[source] < 0) begin
            $error("ready without pending source=%0d", source);
            errors = errors + 1;
          end else begin
            id = pending_id[source];
            accept_cycle[id] = sim_cycle;
            event_state[id] = 2;
            accepted_order[accepted_tail] = id;
            accepted_tail = accepted_tail + 1;
            accepted = accepted + 1;
            source_valid[source] <= 1'b0;
            pending_id[source] = -1;
          end
        end
      end

      // This samples registered retire_valid in the pre-NBA region, exactly as
      // a real always_ff common consumer does.  Post-edge availability is not
      // counted as same-edge consumption.
      if (retire_valid_o) begin
        if (accepted_head >= accepted_tail) begin
          $error("phantom retirement addr=%0d", retire_addr_o);
          errors = errors + 1;
        end else begin
          id = accepted_order[accepted_head];
          accepted_head = accepted_head + 1;
          if (retire_addr_o !== 4'(trace_source[id])) begin
            $error("address/order mismatch id=%0d got=%0d expected=%0d",
                   id, retire_addr_o, trace_source[id]);
            errors = errors + 1;
          end
          delivery_cycle[id] = sim_cycle;
          event_state[id] = 3;
          delivered = delivered + 1;
          delivered_by_source[trace_source[id]] =
            delivered_by_source[trace_source[id]] + 1;
          e2e_sum = e2e_sum + sim_cycle - occurrence[id];
          internal_sum = internal_sum + sim_cycle - accept_cycle[id];
          if ((sim_cycle - occurrence[id]) > max_e2e)
            max_e2e = sim_cycle - occurrence[id];
          if ((sim_cycle - accept_cycle[id]) > max_internal)
            max_internal = sim_cycle - accept_cycle[id];
          if ((sim_cycle - accept_cycle[id]) != 2) begin
            $error("A7 consumer latency mismatch id=%0d accept=%0d delivery=%0d",
                   id, accept_cycle[id], sim_cycle);
            errors = errors + 1;
          end
        end
      end
      if (protocol_fault_o) begin
        $error("owner composition protocol fault");
        errors = errors + 1;
      end
      sim_cycle = sim_cycle + 1;
    end
  end

  task automatic offer(input integer event_index);
    integer offered_source;
    begin
      offered_source = trace_source[event_index];
      if (source_valid[offered_source]) begin
        event_state[event_index] = 1;
        overrun = overrun + 1;
      end else begin
        source_valid[offered_source] = 1'b1;
        pending_id[offered_source] = event_index;
      end
    end
  endtask

  task automatic load_trace;
    begin
      trace_fd = $fopen(trace_path, "r");
      if (trace_fd == 0) $fatal(1, "cannot open prepared trace %s", trace_path);
      scan_count = $fscanf(trace_fd, "%d %d %d %d %d %d %d %d %d\n",
        trace_version, generated, trace_stim_cycles, trace_source_count,
        load_milli, sink_mode, sink_arg0, sink_arg1, trace_seed);
      if (scan_count != 9 || trace_version != 4 || trace_source_count != 16 ||
          generated < 0 || generated > MAX_EVENTS || sink_mode != 0)
        $fatal(1, "unsupported prepared trace header");
      for (id = 0; id < generated; id = id + 1) begin
        scan_count = $fscanf(trace_fd, "%d %d %d %d %d\n", occurrence[id],
          trace_id[id], trace_source[id], trace_address[id], deadline[id]);
        if (scan_count != 5 || trace_id[id] != id ||
            trace_source[id] != trace_address[id] ||
            trace_source[id] < 0 || trace_source[id] >= 16)
          $fatal(1, "malformed prepared event row=%0d", id);
        accept_cycle[id] = -1;
        delivery_cycle[id] = -1;
        event_state[id] = 0;
      end
      $fclose(trace_fd);
    end
  endtask

  task automatic write_results;
    string state_text;
    begin
      event_fd = $fopen(events_path, "w");
      summary_fd = $fopen(summary_path, "w");
      if (event_fd == 0 || summary_fd == 0) $fatal(1, "cannot open result files");
      $fdisplay(event_fd,
        "candidate,test,seed,load_pct,tb_only_event_id,logical_source,source_count,occurrence_cycle,accept_cycle,delivery_cycle,deadline_cycle,observation_end_cycle,event_state");
      for (id = 0; id < generated; id = id + 1) begin
        case (event_state[id])
          1: state_text = "source_overrun";
          2: state_text = "accepted";
          3: state_text = "delivered";
          default: state_text = "pending";
        endcase
        if (accept_cycle[id] < 0)
          $fdisplay(event_fd, "a7-weighted-fovea-ddr,%s,%0d,%0d,%0d,%0d,16,%0d,,,%0d,%0d,%s",
            trace_name, trace_seed, load_milli/10, id, trace_source[id],
            occurrence[id], deadline[id], sim_cycle, state_text);
        else if (delivery_cycle[id] < 0)
          $fdisplay(event_fd, "a7-weighted-fovea-ddr,%s,%0d,%0d,%0d,%0d,16,%0d,%0d,,%0d,%0d,%s",
            trace_name, trace_seed, load_milli/10, id, trace_source[id],
            occurrence[id], accept_cycle[id], deadline[id], sim_cycle, state_text);
        else
          $fdisplay(event_fd, "a7-weighted-fovea-ddr,%s,%0d,%0d,%0d,%0d,16,%0d,%0d,%0d,%0d,%0d,%s",
            trace_name, trace_seed, load_milli/10, id, trace_source[id], occurrence[id],
            accept_cycle[id], delivery_cycle[id], deadline[id], sim_cycle, state_text);
      end
      $fdisplay(summary_fd,
        "candidate,test,seed,load_pct,stim_cycles,generated,source_overrun,accepted,delivered,errors,total_cycles,avg_e2e_latency,max_e2e_latency,avg_internal_latency,max_internal_latency,throughput,fairness,max_request_wait,avg_timing_error,max_timing_error,measurement_delivered,measurement_cycles");
      $fdisplay(summary_fd,
        "a7-weighted-fovea-ddr,%s,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0f,%0d,%0f,%0d,%0f,%0f,%0d,0.0,0,%0d,%0d",
        trace_name, trace_seed, load_milli/10, trace_stim_cycles, generated,
        overrun, accepted, delivered, errors, sim_cycle,
        (delivered != 0) ? real'(e2e_sum)/delivered : 0.0, max_e2e,
        (delivered != 0) ? real'(internal_sum)/delivered : 0.0, max_internal,
        (trace_stim_cycles != 0) ? real'(delivered)/trace_stim_cycles : 0.0,
        service_fairness(), max_request_wait, delivered, trace_stim_cycles);
      $fclose(event_fd);
      $fclose(summary_fd);
    end
  endtask

  initial begin
    if (!$value$plusargs("TRACE_FILE=%s", trace_path) ||
        !$value$plusargs("EVENTS_OUT=%s", events_path) ||
        !$value$plusargs("SUMMARY_OUT=%s", summary_path) ||
        !$value$plusargs("TRACE_NAME=%s", trace_name))
      $fatal(1, "TRACE_FILE/EVENTS_OUT/SUMMARY_OUT/TRACE_NAME required");
    generated = 0; accepted = 0; delivered = 0; overrun = 0; errors = 0;
    accepted_head = 0; accepted_tail = 0; sim_cycle = 0; event_cursor = 0;
    e2e_sum = 0; internal_sum = 0; max_e2e = 0; max_internal = 0;
    max_request_wait = 0;
    traffic_active = 1'b0;
    for (source = 0; source < 16; source = source + 1) begin
      pending_id[source] = -1;
      request_wait[source] = 0;
      delivered_by_source[source] = 0;
    end
    load_trace();

    repeat (3) @(posedge ref_clk_i);
    @(negedge sample_clk_i);
    reset_release_time = $realtime;
    rst_n = 1'b1;
    @(posedge ref_clk_i);
    if (($realtime - reset_release_time) != 4ns)
      $fatal(1, "reset release phase is not exact 4ns");
    while (!dut.endpoint_ready) @(posedge ref_clk_i);
    traffic_active = 1'b1;

    for (stim_cycle = 0; stim_cycle < trace_stim_cycles; stim_cycle = stim_cycle + 1) begin
      @(negedge ref_clk_i);
      while (event_cursor < generated && occurrence[event_cursor] == stim_cycle) begin
        offer(event_cursor);
        event_cursor = event_cursor + 1;
      end
      if (event_cursor < generated && occurrence[event_cursor] < stim_cycle)
        $fatal(1, "trace cursor missed event=%0d", event_cursor);
    end
    if (event_cursor != generated) $fatal(1, "trace not fully consumed");

    timeout = 0;
    while (((|source_valid) || accepted != delivered || !drain_idle_o) && timeout < 20000) begin
      @(negedge ref_clk_i);
      timeout = timeout + 1;
    end
    if (timeout == 20000 || accepted != delivered || accepted + overrun != generated)
      $fatal(1, "conservation/drain failure generated=%0d accepted=%0d delivered=%0d overrun=%0d",
        generated, accepted, delivered, overrun);
    if (errors != 0) $fatal(1, "scoreboard errors=%0d", errors);
    write_results();
    $display("A4_FOVEA_A7_COMMON_TRACE_PASS name=%s generated=%0d accepted=%0d delivered=%0d overrun=%0d latency=2",
      trace_name, generated, accepted, delivered, overrun);
    $finish;
  end
endmodule
