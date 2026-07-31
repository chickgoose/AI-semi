module aer_scoreboard #(
  parameter int NUM_SOURCES = 4,
  parameter int ADDR_WIDTH  = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES),
  parameter int MAX_EVENTS = 65536
) (
  aer_if.monitor bus,
  output integer accepted_count,
  output integer emitted_count,
  output integer error_count,
  output integer max_latency,
  output integer max_wait,
  output integer cycle_count
);
  logic [ADDR_WIDTH-1:0] expected_addr [NUM_SOURCES][MAX_EVENTS];
  integer accepted_cycle [NUM_SOURCES][MAX_EVENTS];
  integer head [NUM_SOURCES];
  integer tail [NUM_SOURCES];
  integer emitted_by_source [NUM_SOURCES];
  integer request_wait [NUM_SOURCES];
  integer latency_sum;
  integer first_measure_cycle;
  integer last_measure_cycle;
  integer src;
  integer latency;
  integer outstanding;
  logic previous_stalled;
  logic [ADDR_WIDTH-1:0] stalled_addr;
  logic [SOURCE_WIDTH-1:0] stalled_src;

  function automatic integer pending_count();
    integer i;
    begin
      pending_count = 0;
      for (i = 0; i < NUM_SOURCES; i = i + 1)
        pending_count = pending_count + tail[i] - head[i];
    end
  endfunction

  function automatic real fairness_index();
    integer i;
    real sum;
    real squares;
    begin
      sum = 0.0;
      squares = 0.0;
      for (i = 0; i < NUM_SOURCES; i = i + 1) begin
        sum = sum + emitted_by_source[i];
        squares = squares + emitted_by_source[i] * emitted_by_source[i];
      end
      if (squares == 0.0)
        fairness_index = 1.0;
      else
        fairness_index = (sum * sum) / (NUM_SOURCES * squares);
    end
  endfunction

  function automatic real average_latency();
    if (emitted_count == 0)
      average_latency = 0.0;
    else
      average_latency = real'(latency_sum) / emitted_count;
  endfunction

  function automatic real throughput();
    integer span;
    begin
      span = last_measure_cycle - first_measure_cycle + 1;
      if ((emitted_count == 0) || (span <= 0))
        throughput = 0.0;
      else
        throughput = real'(emitted_count) / span;
    end
  endfunction

  task automatic check_complete();
    integer missing;
    begin
      missing = pending_count();
      if (missing != 0) begin
        $error("SCOREBOARD missing events: %0d", missing);
        error_count = error_count + missing;
      end
      if (accepted_count != emitted_count) begin
        $error("SCOREBOARD count mismatch accepted=%0d emitted=%0d",
               accepted_count, emitted_count);
        error_count = error_count + 1;
      end
    end
  endtask

  always @(posedge bus.clk or negedge bus.rst_n) begin
    if (!bus.rst_n) begin
      accepted_count = 0;
      emitted_count = 0;
      error_count = 0;
      max_latency = 0;
      max_wait = 0;
      cycle_count = 0;
      latency_sum = 0;
      first_measure_cycle = -1;
      last_measure_cycle = -1;
      previous_stalled = 1'b0;
      stalled_addr = '0;
      stalled_src = '0;
      for (src = 0; src < NUM_SOURCES; src = src + 1) begin
        head[src] = 0;
        tail[src] = 0;
        emitted_by_source[src] = 0;
        request_wait[src] = 0;
      end
    end else begin
      cycle_count = cycle_count + 1;

      if (previous_stalled &&
          ((bus.out_valid !== 1'b1) ||
           (bus.out_addr !== stalled_addr) || (bus.out_src !== stalled_src))) begin
        $error("SCOREBOARD output changed while stalled");
        error_count = error_count + 1;
      end
      previous_stalled = bus.out_valid && !bus.out_ready;
      if (bus.out_valid && !bus.out_ready) begin
        stalled_addr = bus.out_addr;
        stalled_src = bus.out_src;
      end

      for (src = 0; src < NUM_SOURCES; src = src + 1) begin
        if (bus.in_valid[src] && !bus.in_ready[src]) begin
          request_wait[src] = request_wait[src] + 1;
          if (request_wait[src] > max_wait)
            max_wait = request_wait[src];
        end else begin
          request_wait[src] = 0;
        end
        if (bus.in_valid[src] && bus.in_ready[src]) begin
          if ($isunknown(bus.in_addr[src])) begin
            $error("SCOREBOARD unknown input address for source %0d", src);
            error_count = error_count + 1;
          end else if (tail[src] >= MAX_EVENTS) begin
            $error("SCOREBOARD capacity exceeded for source %0d", src);
            error_count = error_count + 1;
          end else begin
            expected_addr[src][tail[src]] = bus.in_addr[src];
            accepted_cycle[src][tail[src]] = cycle_count;
            tail[src] = tail[src] + 1;
            accepted_count = accepted_count + 1;
            if (first_measure_cycle < 0)
              first_measure_cycle = cycle_count;
          end
        end
      end

      if (bus.out_valid && bus.out_ready) begin
        last_measure_cycle = cycle_count;
        if ($isunknown({bus.out_src, bus.out_addr})) begin
          $error("SCOREBOARD unknown output payload");
          error_count = error_count + 1;
        end else if (bus.out_src >= NUM_SOURCES) begin
          $error("SCOREBOARD illegal source id %0d", bus.out_src);
          error_count = error_count + 1;
        end else if (head[bus.out_src] >= tail[bus.out_src]) begin
          $error("SCOREBOARD duplicate/unexpected event src=%0d addr=0x%0h",
                 bus.out_src, bus.out_addr);
          error_count = error_count + 1;
        end else begin
          if (bus.out_addr !== expected_addr[bus.out_src][head[bus.out_src]]) begin
            $error("SCOREBOARD order/data error src=%0d expected=0x%0h actual=0x%0h",
                   bus.out_src, expected_addr[bus.out_src][head[bus.out_src]],
                   bus.out_addr);
            error_count = error_count + 1;
          end
          latency = cycle_count - accepted_cycle[bus.out_src][head[bus.out_src]];
          latency_sum = latency_sum + latency;
          if (latency > max_latency)
            max_latency = latency;
          head[bus.out_src] = head[bus.out_src] + 1;
          emitted_by_source[bus.out_src] = emitted_by_source[bus.out_src] + 1;
          emitted_count = emitted_count + 1;
        end
      end

      for (src = 0; src < NUM_SOURCES; src = src + 1) begin
        if (head[src] < tail[src]) begin
          outstanding = cycle_count - accepted_cycle[src][head[src]];
          if (outstanding > max_wait)
            max_wait = outstanding;
        end
      end
    end
  end
endmodule
