`timescale 1ns/1ps

module a3_bubble_free_compare_tb;
  localparam int NUM_SOURCES = 4;
  localparam int ADDR_WIDTH = 16;
  localparam int SOURCE_WIDTH = 2;
  localparam int STREAM_EVENTS = 64;
  localparam int TIMEOUT_CYCLES = 1000;

  logic clk = 1'b0;
  logic rst_n = 1'b0;
  always #5 clk = ~clk;

  logic [NUM_SOURCES-1:0] baseline_in_valid;
  logic [NUM_SOURCES-1:0] baseline_in_ready;
  logic [ADDR_WIDTH-1:0] baseline_in_addr [NUM_SOURCES];
  logic baseline_out_valid;
  logic baseline_out_ready;
  logic [ADDR_WIDTH-1:0] baseline_out_addr;
  logic [SOURCE_WIDTH-1:0] baseline_out_src;

  logic [NUM_SOURCES-1:0] candidate_in_valid;
  logic [NUM_SOURCES-1:0] candidate_in_ready;
  logic [ADDR_WIDTH-1:0] candidate_in_addr [NUM_SOURCES];
  logic candidate_out_valid;
  logic candidate_out_ready;
  logic [ADDR_WIDTH-1:0] candidate_out_addr;
  logic [SOURCE_WIDTH-1:0] candidate_out_src;

  integer cycle_count;
  integer baseline_accepted;
  integer baseline_emitted;
  integer candidate_accepted;
  integer candidate_emitted;
  integer baseline_accept_cycle [0:STREAM_EVENTS-1];
  integer baseline_emit_cycle [0:STREAM_EVENTS-1];
  integer candidate_accept_cycle [0:STREAM_EVENTS-1];
  integer candidate_emit_cycle [0:STREAM_EVENTS-1];
  logic [ADDR_WIDTH-1:0] baseline_expected [0:STREAM_EVENTS-1];
  logic [ADDR_WIDTH-1:0] candidate_expected [0:STREAM_EVENTS-1];

  aer_dut #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH)
  ) baseline (
    .clk(clk),
    .rst_n(rst_n),
    .in_valid(baseline_in_valid),
    .in_ready(baseline_in_ready),
    .in_addr(baseline_in_addr),
    .out_valid(baseline_out_valid),
    .out_ready(baseline_out_ready),
    .out_addr(baseline_out_addr),
    .out_src(baseline_out_src)
  );

  a3_bubble_free_dut #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH)
  ) candidate (
    .clk(clk),
    .rst_n(rst_n),
    .in_valid(candidate_in_valid),
    .in_ready(candidate_in_ready),
    .in_addr(candidate_in_addr),
    .out_valid(candidate_out_valid),
    .out_ready(candidate_out_ready),
    .out_addr(candidate_out_addr),
    .out_src(candidate_out_src)
  );

  task automatic drive_baseline_stream;
    integer sent;
    begin
      sent = 0;
      @(negedge clk);
      baseline_in_valid[0] = 1'b1;
      baseline_in_addr[0] = ADDR_WIDTH'(16'h1000 + sent);
      while (sent < STREAM_EVENTS) begin
        @(posedge clk);
        if (baseline_in_ready[0]) begin
          sent = sent + 1;
        end
        @(negedge clk);
        if (sent < STREAM_EVENTS) begin
          baseline_in_addr[0] = ADDR_WIDTH'(16'h1000 + sent);
        end else begin
          baseline_in_valid[0] = 1'b0;
          baseline_in_addr[0] = '0;
        end
      end
    end
  endtask

  task automatic drive_candidate_stream;
    integer sent;
    begin
      sent = 0;
      @(negedge clk);
      candidate_in_valid[0] = 1'b1;
      candidate_in_addr[0] = ADDR_WIDTH'(16'h2000 + sent);
      while (sent < STREAM_EVENTS) begin
        @(posedge clk);
        if (candidate_in_ready[0]) begin
          sent = sent + 1;
        end
        @(negedge clk);
        if (sent < STREAM_EVENTS) begin
          candidate_in_addr[0] = ADDR_WIDTH'(16'h2000 + sent);
        end else begin
          candidate_in_valid[0] = 1'b0;
          candidate_in_addr[0] = '0;
        end
      end
    end
  endtask

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      cycle_count = 0;
      baseline_accepted = 0;
      baseline_emitted = 0;
      candidate_accepted = 0;
      candidate_emitted = 0;
    end else begin
      cycle_count = cycle_count + 1;

      if (baseline_in_valid[0] && baseline_in_ready[0]) begin
        if (baseline_accepted >= STREAM_EVENTS) begin
          $fatal(1, "baseline accepted an unexpected extra event");
        end
        baseline_expected[baseline_accepted] = baseline_in_addr[0];
        baseline_accept_cycle[baseline_accepted] = cycle_count;
        baseline_accepted = baseline_accepted + 1;
      end
      if (baseline_out_valid && baseline_out_ready) begin
        if (baseline_emitted >= baseline_accepted) begin
          $fatal(1, "baseline duplicate or unexpected output");
        end
        if ((baseline_out_src !== '0) ||
            (baseline_out_addr !== baseline_expected[baseline_emitted])) begin
          $fatal(1, "baseline reorder/payload corruption at output %0d",
                 baseline_emitted);
        end
        baseline_emit_cycle[baseline_emitted] = cycle_count;
        baseline_emitted = baseline_emitted + 1;
      end

      if (candidate_in_valid[0] && candidate_in_ready[0]) begin
        if (candidate_accepted >= STREAM_EVENTS) begin
          $fatal(1, "candidate accepted an unexpected extra event");
        end
        candidate_expected[candidate_accepted] = candidate_in_addr[0];
        candidate_accept_cycle[candidate_accepted] = cycle_count;
        candidate_accepted = candidate_accepted + 1;
      end
      if (candidate_out_valid && candidate_out_ready) begin
        if (candidate_emitted >= candidate_accepted) begin
          $fatal(1, "candidate duplicate or unexpected output");
        end
        if ((candidate_out_src !== '0) ||
            (candidate_out_addr !== candidate_expected[candidate_emitted])) begin
          $fatal(1, "candidate reorder/payload corruption at output %0d",
                 candidate_emitted);
        end
        candidate_emit_cycle[candidate_emitted] = cycle_count;
        candidate_emitted = candidate_emitted + 1;
      end
    end
  end

  initial begin : run_test
    integer source;
    integer event_index;
    integer timeout;
    real baseline_steady_throughput;
    real candidate_steady_throughput;
    real baseline_average_latency;
    real candidate_average_latency;

    baseline_in_valid = '0;
    candidate_in_valid = '0;
    baseline_out_ready = 1'b1;
    candidate_out_ready = 1'b1;
    for (source = 0; source < NUM_SOURCES; source = source + 1) begin
      baseline_in_addr[source] = '0;
      candidate_in_addr[source] = '0;
    end

    repeat (4) @(posedge clk);
    @(negedge clk);
    rst_n = 1'b1;

    fork
      drive_baseline_stream();
      drive_candidate_stream();
    join

    timeout = 0;
    while (((baseline_emitted < STREAM_EVENTS) ||
            (candidate_emitted < STREAM_EVENTS)) &&
           (timeout < TIMEOUT_CYCLES)) begin
      @(negedge clk);
      timeout = timeout + 1;
    end
    if (timeout >= TIMEOUT_CYCLES) begin
      $fatal(1, "timeout draining comparison streams");
    end
    if ((baseline_accepted != STREAM_EVENTS) ||
        (baseline_emitted != STREAM_EVENTS) ||
        (candidate_accepted != STREAM_EVENTS) ||
        (candidate_emitted != STREAM_EVENTS)) begin
      $fatal(1, "missing event: baseline %0d/%0d candidate %0d/%0d",
             baseline_accepted, baseline_emitted,
             candidate_accepted, candidate_emitted);
    end

    baseline_average_latency = 0.0;
    candidate_average_latency = 0.0;
    for (event_index = 0; event_index < STREAM_EVENTS; event_index = event_index + 1) begin
      baseline_average_latency = baseline_average_latency +
        (baseline_emit_cycle[event_index] - baseline_accept_cycle[event_index]);
      candidate_average_latency = candidate_average_latency +
        (candidate_emit_cycle[event_index] - candidate_accept_cycle[event_index]);
      if (event_index > 0) begin
        if ((baseline_accept_cycle[event_index] -
             baseline_accept_cycle[event_index-1]) != 2) begin
          $fatal(1, "baseline input II changed at event %0d", event_index);
        end
        if ((candidate_accept_cycle[event_index] -
             candidate_accept_cycle[event_index-1]) != 1) begin
          $fatal(1, "candidate input II is not one at event %0d", event_index);
        end
        if ((candidate_emit_cycle[event_index] -
             candidate_emit_cycle[event_index-1]) != 1) begin
          $fatal(1, "candidate output II is not one at event %0d", event_index);
        end
      end
    end
    baseline_average_latency = baseline_average_latency / STREAM_EVENTS;
    candidate_average_latency = candidate_average_latency / STREAM_EVENTS;
    baseline_steady_throughput = real'(STREAM_EVENTS) /
      (baseline_emit_cycle[STREAM_EVENTS-1] - baseline_emit_cycle[0] + 1);
    candidate_steady_throughput = real'(STREAM_EVENTS) /
      (candidate_emit_cycle[STREAM_EVENTS-1] - candidate_emit_cycle[0] + 1);

    if (candidate_steady_throughput != 1.0) begin
      $fatal(1, "candidate steady throughput is not 1 event/cycle");
    end
    $display("A3_COMPARE baseline accepted=%0d emitted=%0d avg_latency=%0.4f input_ii=2 steady_throughput=%0.6f",
      baseline_accepted, baseline_emitted, baseline_average_latency,
      baseline_steady_throughput);
    $display("A3_COMPARE candidate accepted=%0d emitted=%0d avg_latency=%0.4f input_ii=1 output_ii=1 steady_throughput=%0.6f",
      candidate_accepted, candidate_emitted, candidate_average_latency,
      candidate_steady_throughput);
    $display("PASS: A3 bubble-free TX continuous-stream comparison");
    $finish;
  end
endmodule
