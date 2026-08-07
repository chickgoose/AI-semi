`timescale 1ns/1ps

module a2_phase3_physical_tb #(
  parameter int NUM_SOURCES = 16,
  parameter int MODEL = 0,
  parameter int ADDR_WIDTH = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES),
  parameter int MAX_EVENTS = 4096
);
  logic clk = 1'b0;
  logic rst_n;
  logic [NUM_SOURCES-1:0] source_valid;
  logic [NUM_SOURCES-1:0] offered_this_cycle;
  logic [NUM_SOURCES-1:0] source_ready;
  logic [NUM_SOURCES*ADDR_WIDTH-1:0] source_event;
  logic retire_valid;
  logic retire_ready;
  logic [ADDR_WIDTH-1:0] retire_event;
  logic [SOURCE_WIDTH-1:0] retire_source;

  logic [ADDR_WIDTH-1:0] expected_event [NUM_SOURCES][MAX_EVENTS];
  integer expected_occurrence [NUM_SOURCES][MAX_EVENTS];
  integer expected_head [NUM_SOURCES];
  integer expected_tail [NUM_SOURCES];
  integer pending_occurrence [NUM_SOURCES];
  integer source_sequence [NUM_SOURCES];
  integer latencies [MAX_EVENTS];
  integer generated_count;
  integer overrun_count;
  integer duplicate_overrun_count;
  integer backpressure_overrun_count;
  integer accepted_count;
  integer delivered_count;
  integer fixed_delivered;
  integer error_count;
  integer latency_count;
  integer clock_cycle;
  integer stim_cycle;
  integer stim_cycles;
  integer source;
  integer index;
  integer other;
  integer epoch;
  integer burst;
  integer width;
  integer group;
  integer base;
  integer offered_source;
  integer watchdog;
  integer latency;
  integer swap_value;
  integer p95;
  integer p99;
  logic stimulus_active;
  string workload;
  string vcd_path;
  string design_name;

  always #5 clk = ~clk;

  a2_phase3_physical_wrapper #(
    .NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH), .MODEL(MODEL)
  ) dut (
    .clk_i(clk), .rst_ni(rst_n),
    .source_valid_i(source_valid), .source_ready_o(source_ready),
    .source_event_i(source_event), .retire_valid_o(retire_valid),
    .retire_ready_i(retire_ready), .retire_event_o(retire_event),
    .retire_source_o(retire_source)
  );

  task automatic offer(input integer selected_source);
    integer encoded;
    begin
      generated_count = generated_count + 1;
      if (offered_this_cycle[selected_source]) begin
        overrun_count = overrun_count + 1;
        duplicate_overrun_count = duplicate_overrun_count + 1;
      end else begin
        offered_this_cycle[selected_source] = 1'b1;
        if (!source_ready[selected_source]) begin
          overrun_count = overrun_count + 1;
          backpressure_overrun_count = backpressure_overrun_count + 1;
        end else begin
          encoded = (selected_source << 10) | source_sequence[selected_source];
          source_valid[selected_source] = 1'b1;
          source_event[selected_source*ADDR_WIDTH +: ADDR_WIDTH] =
            ADDR_WIDTH'(encoded);
          pending_occurrence[selected_source] = clock_cycle;
          source_sequence[selected_source] = source_sequence[selected_source] + 1;
        end
      end
    end
  endtask

  task automatic report_error(input string message);
    begin
      $error("A2_PHASE3 %s", message);
      error_count = error_count + 1;
    end
  endtask

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      clock_cycle = 0;
      accepted_count = 0;
      delivered_count = 0;
      fixed_delivered = 0;
      latency_count = 0;
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        expected_head[source] = 0;
        expected_tail[source] = 0;
      end
    end else begin
      clock_cycle = clock_cycle + 1;
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        if (source_valid[source] && source_ready[source]) begin
          expected_event[source][expected_tail[source]] =
            source_event[source*ADDR_WIDTH +: ADDR_WIDTH];
          expected_occurrence[source][expected_tail[source]] =
            pending_occurrence[source];
          expected_tail[source] = expected_tail[source] + 1;
          accepted_count = accepted_count + 1;
        end
      end
      if (retire_valid && retire_ready) begin
        if (retire_source >= NUM_SOURCES) begin
          report_error("illegal retire source");
        end else if (expected_head[retire_source] >= expected_tail[retire_source]) begin
          report_error("phantom or duplicate retirement");
        end else begin
          if (retire_event !== expected_event[retire_source][expected_head[retire_source]])
            report_error("payload corruption or source-local reorder");
          latency = clock_cycle -
            expected_occurrence[retire_source][expected_head[retire_source]];
          latencies[latency_count] = latency;
          latency_count = latency_count + 1;
          expected_head[retire_source] = expected_head[retire_source] + 1;
          delivered_count = delivered_count + 1;
          if (stimulus_active)
            fixed_delivered = fixed_delivered + 1;
        end
      end
    end
  end

  initial begin
    if (MODEL == 0)
      design_name = "a2";
    else if (MODEL == 1)
      design_name = "flat_rr";
    else
      design_name = "always_buffered";
    if (!$value$plusargs("WORKLOAD=%s", workload))
      workload = "sparse";
    if (!$value$plusargs("VCD=%s", vcd_path))
      vcd_path = "/tmp/a2-phase3.vcd";
    $dumpfile(vcd_path);
    $dumpvars(0, dut);
    $dumpoff;

    rst_n = 1'b0;
    retire_ready = 1'b1;
    source_valid = '0;
    offered_this_cycle = '0;
    stimulus_active = 1'b0;
    generated_count = 0;
    overrun_count = 0;
    duplicate_overrun_count = 0;
    backpressure_overrun_count = 0;
    error_count = 0;
    source_event = '0;
    for (source = 0; source < NUM_SOURCES; source = source + 1) begin
      pending_occurrence[source] = 0;
      source_sequence[source] = 0;
    end
    repeat (4) @(posedge clk);
    @(negedge clk);
    rst_n = 1'b1;
    $dumpon;
    stimulus_active = 1'b1;

    if (workload == "sparse")
      stim_cycles = 320;
    else if ((workload == "hotspot_fixed") || (workload == "recurrence"))
      stim_cycles = 448;
    else if (workload == "oscillate_4")
      stim_cycles = 512;
    else
      $fatal(1, "unknown A2 phase3 workload %s", workload);

    for (stim_cycle = 0; stim_cycle < stim_cycles;
         stim_cycle = stim_cycle + 1) begin
      @(negedge clk);
      source_valid = '0;
      offered_this_cycle = '0;
      if (workload == "sparse") begin
        if ((stim_cycle >= 8) && (stim_cycle < 256) &&
            ((stim_cycle % 8) == 0))
          offer((stim_cycle / 8) % NUM_SOURCES);
      end else if (workload == "hotspot_fixed") begin
        width = (NUM_SOURCES / 4 < 8) ? NUM_SOURCES / 4 : 8;
        for (epoch = 24; epoch <= 280; epoch = epoch + 128)
          for (burst = 0; burst < 5; burst = burst + 1)
            if (stim_cycle == epoch + burst*3)
              for (index = 0; index < width; index = index + 1)
                offer((index*4) % NUM_SOURCES);
      end else if (workload == "recurrence") begin
        group = (NUM_SOURCES < 6) ? NUM_SOURCES : 6;
        for (epoch = 24; epoch <= 280; epoch = epoch + 128)
          for (burst = 0; burst < 12; burst = burst + 1)
            if (stim_cycle == epoch + burst*2) begin
              base = (epoch + burst) % NUM_SOURCES;
              for (index = 0; index < group; index = index + 1)
                offer((base + index*4) % NUM_SOURCES);
            end
      end else if (workload == "oscillate_4") begin
        if ((stim_cycle >= 24) && (stim_cycle < 280) &&
            ((((stim_cycle-24)/4) % 2) == 1))
          for (index = 0; index < 2; index = index + 1)
            offer((stim_cycle + index*3) % NUM_SOURCES);
        if ((stim_cycle >= 352) && (stim_cycle < 448) &&
            ((stim_cycle % 8) == 0))
          offer((stim_cycle/8) % NUM_SOURCES);
      end
    end

    @(negedge clk);
    source_valid = '0;
    stimulus_active = 1'b0;
    watchdog = 0;
    while ((delivered_count != accepted_count) && (watchdog < 8192)) begin
      @(negedge clk);
      watchdog = watchdog + 1;
    end
    if (watchdog >= 8192)
      report_error("drain timeout");
    repeat (4) begin
      @(negedge clk);
      if (retire_valid)
        report_error("late phantom after drain");
    end
    $dumpoff;

    if (accepted_count != delivered_count)
      report_error("accepted/delivered mismatch");
    for (source = 0; source < NUM_SOURCES; source = source + 1)
      if (expected_head[source] != expected_tail[source])
        report_error("per-source scoreboard did not drain");

    for (index = 0; index < latency_count; index = index + 1)
      for (other = index + 1; other < latency_count; other = other + 1)
        if (latencies[other] < latencies[index]) begin
          swap_value = latencies[index];
          latencies[index] = latencies[other];
          latencies[other] = swap_value;
        end
    p95 = latencies[((latency_count*95 + 99)/100)-1];
    p99 = latencies[((latency_count*99 + 99)/100)-1];

    if (error_count == 0) begin
      $display("A2_PHASE3_METRIC design=%s n=%0d workload=%s generated=%0d overrun=%0d duplicate_overrun=%0d backpressure_overrun=%0d accepted=%0d delivered=%0d fixed_delivered=%0d stim_cycles=%0d p95=%0d p99=%0d drain=%0d errors=0",
        design_name, NUM_SOURCES, workload, generated_count, overrun_count,
        duplicate_overrun_count, backpressure_overrun_count, accepted_count,
        delivered_count, fixed_delivered, stim_cycles, p95, p99, watchdog);
      $finish;
    end
    $fatal(1, "A2_PHASE3_FAIL errors=%0d", error_count);
  end
endmodule
