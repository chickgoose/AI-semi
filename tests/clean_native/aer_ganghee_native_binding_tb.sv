`timescale 1ns/1ps

module aer_ganghee_native_binding_tb;
  localparam int NUM_SOURCES = 16;
  localparam int ADDR_WIDTH = 16;
  localparam int RETIRE_LANES = 1;

  logic clk = 1'b0;
  always #5 clk = ~clk;

  aer_bench_if #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(RETIRE_LANES)
  ) bench(clk);

  aer_ganghee_native_binding #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(RETIRE_LANES)
  ) binding(bench);

  integer acknowledgement_count = 0;
  integer test_errors = 0;
  integer source;
  integer acknowledged_source;
  integer timeout;
  integer count_before_idle;
  integer issued_count = 0;
  integer native_result_count = 0;
  integer duplicate_count = 0;
  integer masked_sampling_edge_count = 0;

  task automatic issue_event(input integer event_source,
                             input logic [ADDR_WIDTH-1:0] event_value);
    begin
      if (bench.source_valid[event_source]) begin
        test_errors = test_errors + 1;
        $error("BINDING_TB issue while source pending source=%0d", event_source);
      end
      bench.source_event[event_source] = event_value;
      bench.source_valid[event_source] = 1'b1;
      issued_count = issued_count + 1;
    end
  endtask

  always @(posedge clk) begin
    if (bench.rst_n && binding.native_valid) begin
      native_result_count = native_result_count + 1;
      if (binding.native_req[binding.native_addr]) begin
        test_errors = test_errors + 1;
        $error("BINDING_TB acknowledged req still high at sampling edge source=%0d",
               binding.native_addr);
      end else begin
        masked_sampling_edge_count = masked_sampling_edge_count + 1;
      end
      if (!bench.source_valid[binding.native_addr]) begin
        duplicate_count = duplicate_count + 1;
        test_errors = test_errors + 1;
        $error("BINDING_TB duplicate native result source=%0d",
               binding.native_addr);
      end
    end
    if (bench.rst_n && (|bench.source_ready)) begin
      acknowledged_source = -1;
      for (source = 0; source < NUM_SOURCES; source = source + 1)
        if (bench.source_ready[source])
          acknowledged_source = source;
      if (acknowledged_source < 0) begin
        test_errors = test_errors + 1;
        $error("BINDING_TB ready asserted without source");
      end else begin
        if (!bench.retire_valid[0]) begin
          test_errors = test_errors + 1;
          $error("BINDING_TB implicit acknowledge missing retire_valid");
        end
        if (bench.retire_source[0] !== 4'(acknowledged_source)) begin
          test_errors = test_errors + 1;
          $error("BINDING_TB source mismatch ready=%0d retire=%0d",
                 acknowledged_source, bench.retire_source[0]);
        end
        if (bench.retire_event[0] !== ADDR_WIDTH'(acknowledged_source)) begin
          test_errors = test_errors + 1;
          $error("BINDING_TB native-address derivation mismatch source=%0d retire=%h canary=%h",
                 acknowledged_source, bench.retire_event[0],
                 bench.source_event[acknowledged_source]);
        end
        acknowledgement_count = acknowledgement_count + 1;
        // Mirror the common scoreboard's exactly-once pending clear.  NBA
        // deasserts req immediately after the observing edge.
        bench.source_valid[acknowledged_source] <= 1'b0;
      end
    end
  end

  task automatic wait_for_acknowledgements(input integer expected_count);
    begin
      timeout = 0;
      while ((acknowledgement_count < expected_count) && (timeout < 64)) begin
        @(negedge clk);
        timeout = timeout + 1;
      end
      if (acknowledgement_count != expected_count) begin
        test_errors = test_errors + 1;
        $error("BINDING_TB acknowledgement timeout expected=%0d actual=%0d",
               expected_count, acknowledgement_count);
      end
    end
  endtask

  initial begin
    bench.rst_n = 1'b0;
    bench.source_valid = '0;
    bench.retire_ready = '1;
    for (source = 0; source < NUM_SOURCES; source = source + 1)
      bench.source_event[source] = '0;

    repeat (4) @(posedge clk);
    @(negedge clk);
    bench.rst_n = 1'b1;

    // Simultaneous pending sources: the native DUT owns selection.  The
    // binding acknowledges only the returned addr and adds no arbitration.
    // These free metadata canaries deliberately disagree with the native
    // address.  A binding that copies source_event will fail this test.
    issue_event(2, 16'ha522);
    issue_event(7, 16'h5a77);
    issue_event(15, 16'hc3ff);
    wait_for_acknowledgements(3);

    // Fastest legal same-source retrigger: the first acknowledgement clears
    // req in the NBA region; the driver waits until the following negedge,
    // observes req low, then presents the next event.  This prevents a held
    // request from being interpreted as a duplicate native completion.
    @(negedge clk);
    issue_event(5, 16'hde51);
    wait_for_acknowledgements(4);
    @(negedge clk);
    if (bench.source_valid[5] !== 1'b0) begin
      test_errors = test_errors + 1;
      $error("BINDING_TB req did not clear after implicit acknowledge");
    end
    issue_event(5, 16'had52);
    wait_for_acknowledgements(5);

    count_before_idle = acknowledgement_count;
    repeat (4) @(posedge clk);
    if (acknowledgement_count != count_before_idle) begin
      test_errors = test_errors + 1;
      $error("BINDING_TB duplicate completion after req clear");
    end

    if ((issued_count != acknowledgement_count) ||
        (native_result_count != acknowledgement_count) ||
        (masked_sampling_edge_count != acknowledgement_count)) begin
      test_errors = test_errors + 1;
      $error("BINDING_TB count mismatch issued=%0d native=%0d masked_edges=%0d ack=%0d",
             issued_count, native_result_count, masked_sampling_edge_count,
             acknowledgement_count);
    end
    if (test_errors == 0)
      $display("GANGHEE_NATIVE_BINDING_PASS issued=%0d acknowledgements=%0d native_results=%0d masked_sampling_edges=%0d duplicates=%0d",
               issued_count, acknowledgement_count, native_result_count,
               masked_sampling_edge_count, duplicate_count);
    else
      $fatal(1, "GANGHEE_NATIVE_BINDING_FAIL errors=%0d", test_errors);
    $finish;
  end
endmodule
