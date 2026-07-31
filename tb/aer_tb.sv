`timescale 1ns/1ps

module aer_tb;
  parameter int NUM_SOURCES = 4;
  parameter int ADDR_WIDTH = 16;
  parameter int EVENTS_PER_SOURCE = 32;
  parameter int TIMEOUT_CYCLES = 10000;

  logic clk = 1'b0;
  always #5 clk = ~clk;

  aer_if #(.NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH)) bus(clk);
  dut_adapter #(.NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH)) dut(bus);

  integer accepted_count;
  integer emitted_count;
  integer error_count;
  integer max_latency;
  integer max_wait;
  integer cycle_count;
  aer_scoreboard #(.NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH)) sb (
    bus, accepted_count, emitted_count, error_count,
    max_latency, max_wait, cycle_count
  );

  string test_name;
  string metrics_path;
  integer metrics_fd;
  integer timeout;
  integer i;

  function automatic logic [ADDR_WIDTH-1:0] make_event(input integer source,
                                                        input integer sequence);
    make_event = ADDR_WIDTH'((source << (ADDR_WIDTH/2)) ^ sequence);
  endfunction

  task automatic send_burst(input integer source, input integer count);
    integer sequence;
    integer request_timeout;
    begin
      sequence = 0;
      request_timeout = 0;
      @(negedge clk);
      while (sequence < count) begin
        bus.in_valid[source] = 1'b1;
        bus.in_addr[source] = make_event(source, sequence);
        @(posedge clk);
        if (bus.in_ready[source]) begin
          sequence = sequence + 1;
          request_timeout = 0;
        end else begin
          request_timeout = request_timeout + 1;
          if (request_timeout >= TIMEOUT_CYCLES)
            $fatal(1, "Source %0d request timed out", source);
        end
        @(negedge clk);
      end
      bus.in_valid[source] = 1'b0;
      bus.in_addr[source] = '0;
    end
  endtask

  task automatic reset_dut();
    begin
      bus.rst_n = 1'b0;
      bus.in_valid = '0;
      bus.out_ready = 1'b0;
      for (i = 0; i < NUM_SOURCES; i = i + 1)
        bus.in_addr[i] = '0;
      repeat (4) @(posedge clk);
      @(negedge clk);
      bus.rst_n = 1'b1;
      bus.out_ready = 1'b1;
    end
  endtask

  task automatic run_single();
    begin
      send_burst(0, EVENTS_PER_SOURCE);
    end
  endtask

  task automatic run_simultaneous();
    begin
      fork
        send_burst(0, EVENTS_PER_SOURCE);
        send_burst(1, EVENTS_PER_SOURCE);
        send_burst(2, EVENTS_PER_SOURCE);
        send_burst(3, EVENTS_PER_SOURCE);
      join
    end
  endtask

  task automatic run_burst();
    begin
      fork
        send_burst(0, EVENTS_PER_SOURCE * 4);
        send_burst(1, EVENTS_PER_SOURCE * 2);
        send_burst(2, EVENTS_PER_SOURCE * 3);
        send_burst(3, EVENTS_PER_SOURCE);
      join
    end
  endtask

  task automatic apply_backpressure();
    begin
      while (bus.rst_n) begin
        repeat (2) @(negedge clk) bus.out_ready = 1'b1;
        repeat (3) @(negedge clk) bus.out_ready = 1'b0;
      end
    end
  endtask

  task automatic run_backpressure();
    begin
      fork : bp_workers
        apply_backpressure();
        begin
          fork
            send_burst(0, EVENTS_PER_SOURCE);
            send_burst(1, EVENTS_PER_SOURCE);
            send_burst(2, EVENTS_PER_SOURCE);
            send_burst(3, EVENTS_PER_SOURCE);
          join
        end
      join_any
      disable bp_workers;
      @(negedge clk) bus.out_ready = 1'b1;
    end
  endtask

  task automatic drain_and_report();
    begin
      timeout = 0;
      while ((sb.pending_count() != 0 || bus.out_valid) &&
             timeout < TIMEOUT_CYCLES) begin
        @(negedge clk);
        timeout = timeout + 1;
      end
      if (timeout >= TIMEOUT_CYCLES) begin
        $error("TIMEOUT after %0d drain cycles", TIMEOUT_CYCLES);
        sb.error_count = sb.error_count + 1;
      end
      @(negedge clk);
      sb.check_complete();

      metrics_fd = $fopen(metrics_path, "w");
      if (metrics_fd == 0) begin
        $error("Cannot open metrics path: %s", metrics_path);
        sb.error_count = sb.error_count + 1;
      end else begin
        $fdisplay(metrics_fd,
          "test,accepted,emitted,errors,cycles,avg_latency_cycles,max_latency_cycles,throughput_events_per_cycle,fairness_jain,max_wait_cycles");
        $fdisplay(metrics_fd, "%s,%0d,%0d,%0d,%0d,%0.4f,%0d,%0.6f,%0.6f,%0d",
          test_name, accepted_count, emitted_count, error_count, cycle_count,
          sb.average_latency(), max_latency, sb.throughput(),
          sb.fairness_index(), max_wait);
        $fclose(metrics_fd);
      end

      $display("AER_METRICS test=%s accepted=%0d emitted=%0d errors=%0d avg_latency=%0.4f max_latency=%0d throughput=%0.6f fairness=%0.6f max_wait=%0d",
        test_name, accepted_count, emitted_count, error_count,
        sb.average_latency(), max_latency, sb.throughput(),
        sb.fairness_index(), max_wait);
      if (error_count == 0)
        $display("AER_TEST_PASS %s", test_name);
      else
        $fatal(1, "AER_TEST_FAIL %s errors=%0d", test_name, error_count);
    end
  endtask

  initial begin
    if (!$value$plusargs("TEST=%s", test_name))
      test_name = "single";
    if (!$value$plusargs("METRICS=%s", metrics_path))
      metrics_path = "aer_metrics.csv";

    reset_dut();
    case (test_name)
      "single":       run_single();
      "simultaneous": run_simultaneous();
      "burst":        run_burst();
      "backpressure": run_backpressure();
      default: $fatal(2, "Unknown TEST=%s", test_name);
    endcase
    drain_and_report();
    $finish;
  end
endmodule
