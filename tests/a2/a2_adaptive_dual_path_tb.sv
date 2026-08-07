`timescale 1ns/1ps

module a2_adaptive_dual_path_tb;
  localparam int NUM_SOURCES = 8;
  localparam int ADDR_WIDTH = 16;
  localparam int SOURCE_WIDTH = $clog2(NUM_SOURCES);

  logic clk = 1'b0;
  logic rst_n;
  logic [NUM_SOURCES-1:0] source_valid;
  logic [NUM_SOURCES-1:0] source_ready;
  logic [ADDR_WIDTH-1:0] source_event [NUM_SOURCES];
  logic retire_valid;
  logic retire_ready;
  logic [ADDR_WIDTH-1:0] retire_event;
  logic [SOURCE_WIDTH-1:0] retire_source;
  logic [NUM_SOURCES-1:0] sampled_ready;
  logic sampled_retire_valid;
  logic [ADDR_WIDTH-1:0] sampled_retire_event;
  logic [SOURCE_WIDTH-1:0] sampled_retire_source;

  logic [ADDR_WIDTH-1:0] expected [NUM_SOURCES][128];
  integer expected_head [NUM_SOURCES];
  integer expected_tail [NUM_SOURCES];
  integer accepted_count;
  integer delivered_count;
  integer error_count;
  integer source;
  integer batch;
  integer watchdog;

  always #5 clk = ~clk;

  a2_adaptive_dual_path_core #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RESERVOIR_DEPTH(8),
    .ENTER_LEVEL(4),
    .EXIT_LEVEL(1),
    .QUIET_CYCLES(3)
  ) dut (
    .clk_i(clk),
    .rst_ni(rst_n),
    .source_valid_i(source_valid),
    .source_ready_o(source_ready),
    .source_event_i(source_event),
    .retire_valid_o(retire_valid),
    .retire_ready_i(retire_ready),
    .retire_event_o(retire_event),
    .retire_source_o(retire_source)
  );

  task automatic fail(input string message);
    begin
      $error("A2_DIRECTED %s", message);
      error_count = error_count + 1;
    end
  endtask

  task automatic clear_inputs;
    integer clear_source;
    begin
      source_valid = '0;
      for (clear_source = 0; clear_source < NUM_SOURCES;
           clear_source = clear_source + 1)
        source_event[clear_source] = '0;
    end
  endtask

  task automatic apply_reset;
    integer reset_source;
    begin
      @(negedge clk);
      rst_n = 1'b0;
      clear_inputs();
      repeat (3) @(posedge clk);
      @(negedge clk);
      rst_n = 1'b1;
      for (reset_source = 0; reset_source < NUM_SOURCES;
           reset_source = reset_source + 1) begin
        expected_head[reset_source] = 0;
        expected_tail[reset_source] = 0;
      end
      accepted_count = 0;
      delivered_count = 0;
    end
  endtask

  task automatic offer(input integer selected_source,
                       input logic [ADDR_WIDTH-1:0] event_value);
    begin
      source_valid[selected_source] = 1'b1;
      source_event[selected_source] = event_value;
    end
  endtask

  task automatic wait_for_empty;
    begin
      watchdog = 0;
      while (((dut.reservoir_count != 0) || retire_valid) && (watchdog < 32)) begin
        @(posedge clk);
        #1;
        watchdog = watchdog + 1;
      end
      if (watchdog >= 32)
        fail("reservoir failed to drain");
    end
  endtask

  always @(posedge clk) begin
    sampled_ready = source_ready;
    sampled_retire_valid = retire_valid;
    sampled_retire_event = retire_event;
    sampled_retire_source = retire_source;
    if (rst_n) begin
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        if (source_valid[source] && source_ready[source]) begin
          expected[source][expected_tail[source]] = source_event[source];
          expected_tail[source] = expected_tail[source] + 1;
          accepted_count = accepted_count + 1;
        end
      end
      if (retire_valid && retire_ready) begin
        if (retire_source >= NUM_SOURCES) begin
          fail("illegal retire source");
        end else if (expected_head[retire_source] >= expected_tail[retire_source]) begin
          fail("phantom or duplicate retirement");
        end else begin
          if (retire_event !== expected[retire_source][expected_head[retire_source]])
            fail("source-local ordering or payload mismatch");
          expected_head[retire_source] = expected_head[retire_source] + 1;
          delivered_count = delivered_count + 1;
        end
      end
    end
  end

  initial begin
    rst_n = 1'b0;
    retire_ready = 1'b1;
    error_count = 0;
    clear_inputs();

    // Isolated sparse traffic must neither write nor read the reservoir.
    apply_reset();
    @(negedge clk);
    offer(4, 16'h4401);
    @(posedge clk);
    #1;
    if (!(sampled_ready[4] && sampled_retire_valid &&
          (sampled_retire_source == 4) && (sampled_retire_event == 16'h4401)))
      fail("isolated event did not use direct bypass");
    if (dut.reservoir_count != 0)
      fail("isolated event touched the reservoir");
    @(negedge clk);
    clear_inputs();
    @(posedge clk);
    #1;
    if (retire_valid)
      fail("phantom retirement after isolated bypass");

    // Three-way fan-in: one direct completion plus two bank writes on one edge.
    apply_reset();
    @(negedge clk);
    offer(0, 16'h1000);
    offer(1, 16'h1001);
    offer(2, 16'h1002);
    @(posedge clk);
    #1;
    if (sampled_ready[2:0] !== 3'b111)
      fail("direct plus dual-bank admission did not accept three events");
    if (!sampled_retire_valid || (sampled_retire_source != 0) ||
        (sampled_retire_event != 16'h1000))
      fail("wrong direct event under fan-in");
    if (dut.reservoir_count != 2)
      fail("fan-in did not leave two queued events");

    // On the next edge, retire the oldest entry while accepting two at the tail.
    @(negedge clk);
    clear_inputs();
    offer(3, 16'h1003);
    offer(4, 16'h1004);
    @(posedge clk);
    #1;
    if (!sampled_retire_valid || (sampled_retire_source != 1) ||
        (sampled_retire_event != 16'h1001))
      fail("queued head lost priority over new arrivals");
    if (!(sampled_ready[3] && sampled_ready[4]))
      fail("dual-bank enqueue did not overlap dequeue");
    @(negedge clk);
    clear_inputs();
    wait_for_empty();
    if ((accepted_count != 5) || (delivered_count != 5))
      fail("simultaneous bypass/queue conservation failed");

    // A later event from a source already in the reservoir must remain behind it.
    apply_reset();
    @(negedge clk);
    offer(0, 16'h2000);
    offer(5, 16'h2501);
    offer(6, 16'h2601);
    @(posedge clk);
    #1;
    @(negedge clk);
    clear_inputs();
    offer(5, 16'h2502);
    @(posedge clk);
    #1;
    if (!sampled_retire_valid || (sampled_retire_source != 5) ||
        (sampled_retire_event != 16'h2501))
      fail("new same-source event overtook queued predecessor");
    if (!sampled_ready[5])
      fail("same-source tail admission was unexpectedly blocked");
    @(negedge clk);
    clear_inputs();
    wait_for_empty();
    if ((accepted_count != 4) || (delivered_count != 4))
      fail("same-source conservation failed");

    // Repeated direct+two-bank batches force pointer wraparound in both banks.
    apply_reset();
    for (batch = 0; batch < 12; batch = batch + 1) begin
      @(negedge clk);
      clear_inputs();
      offer((batch*3) % NUM_SOURCES, 16'h3000 + batch*3);
      offer((batch*3+1) % NUM_SOURCES, 16'h3001 + batch*3);
      offer((batch*3+2) % NUM_SOURCES, 16'h3002 + batch*3);
      @(posedge clk);
      #1;
      @(negedge clk);
      clear_inputs();
      wait_for_empty();
    end
    if ((accepted_count != 36) || (delivered_count != 36))
      fail("bank wraparound conservation failed");

    // Burst mode must persist after drain for a bounded quiet dwell, then recover.
    apply_reset();
    @(negedge clk);
    offer(0, 16'h4000);
    offer(1, 16'h4001);
    offer(2, 16'h4002);
    @(posedge clk);
    #1;
    if (!dut.burst_mode)
      fail("fan-in did not enter burst mode");
    @(negedge clk);
    clear_inputs();
    wait_for_empty();
    if (!dut.burst_mode)
      fail("burst mode exited without hysteresis dwell");
    watchdog = 0;
    while (dut.burst_mode && (watchdog < 10)) begin
      @(posedge clk);
      #1;
      watchdog = watchdog + 1;
    end
    if (dut.burst_mode)
      fail("burst mode failed to recover after quiet dwell");
    repeat (4) begin
      @(posedge clk);
      #1;
      if (retire_valid)
        fail("late phantom after mode recovery");
    end

    if (accepted_count != delivered_count)
      fail("final accepted/delivered mismatch");
    if (error_count == 0) begin
      $display("A2_DIRECTED_PASS accepted=%0d delivered=%0d", accepted_count,
               delivered_count);
      $finish;
    end
    $fatal(1, "A2_DIRECTED_FAIL errors=%0d", error_count);
  end
endmodule
