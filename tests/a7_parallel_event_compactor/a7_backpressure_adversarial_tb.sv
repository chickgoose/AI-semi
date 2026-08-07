`timescale 1ns/1ps

module a7_backpressure_adversarial_tb;
  parameter int N = 16;
  parameter int K = 4;
  parameter int REFERENCE = 0;
  localparam int AW = 8;
  localparam int SW = $clog2(N);

  logic clk = 0;
  always #5 clk = ~clk;
  logic rst_n;
  logic [N-1:0] source_valid;
  logic [N-1:0][AW-1:0] source_event;
  logic [N-1:0] source_ready;
  logic [K-1:0] retire_valid;
  logic [K-1:0][AW-1:0] retire_event;
  logic [K-1:0][SW-1:0] retire_source;
  logic [K-1:0] retire_ready;
  integer accepted [N];
  integer delivered [N];
  integer source;
  integer lane;
  integer other_lane_progress;
  integer before_progress;
  integer cycle_index;
  logic [AW-1:0] held_event;
  logic [SW-1:0] held_source;

  generate
    if (REFERENCE != 0) begin : reference_dut
      a7_replicated_selector_reference #(
        .NUM_SOURCES(N), .ADDR_WIDTH(AW), .RETIRE_LANES(K)
      ) dut (.*);
    end else begin : prefix_dut
      a7_parallel_event_compactor #(
        .NUM_SOURCES(N), .ADDR_WIDTH(AW), .RETIRE_LANES(K)
      ) dut (.*);
    end
  endgenerate

  always @(posedge clk) begin
    if (rst_n) begin
      for (source = 0; source < N; source = source + 1)
        if (source_valid[source] && source_ready[source])
          accepted[source] = accepted[source] + 1;
      for (lane = 0; lane < K; lane = lane + 1) begin
        if (retire_valid[lane]) begin
          if (retire_event[lane] != AW'(retire_source[lane]))
            $fatal(1, "event/source mismatch lane=%0d", lane);
          for (source = lane+1; source < K; source = source + 1)
            if (retire_valid[source] &&
                retire_source[source] == retire_source[lane])
              $fatal(1, "duplicate inflight source=%0d lanes=%0d,%0d",
                     retire_source[lane], lane, source);
        end
        if (retire_valid[lane] && retire_ready[lane]) begin
          source = integer'(retire_source[lane]);
          if (delivered[source] >= accepted[source])
            $fatal(1, "phantom/duplicate retirement source=%0d", source);
          delivered[source] = delivered[source] + 1;
          if (lane != 0) other_lane_progress = other_lane_progress + 1;
        end
      end
    end
  end

  task automatic drive_independent(input integer step);
    begin
      for (lane = 0; lane < K; lane = lane + 1)
        retire_ready[lane] = (((step + 2*lane) % (lane+2)) != 0);
    end
  endtask

  initial begin
    rst_n = 0;
    source_valid = '0;
    retire_ready = '0;
    other_lane_progress = 0;
    for (source = 0; source < N; source = source + 1) begin
      accepted[source] = 0;
      delivered[source] = 0;
      source_event[source] = AW'(source);
    end
    repeat (3) @(posedge clk);
    @(negedge clk);
    rst_n = 1;
    source_valid = '1;

    // Every lane sees a distinct periodic ready waveform.
    for (cycle_index = 0; cycle_index < 96; cycle_index = cycle_index + 1) begin
      drive_independent(cycle_index);
      @(posedge clk); @(negedge clk);
    end

    // All-ready and lane-alternating cycles stress same-edge refill.
    for (cycle_index = 0; cycle_index < 64; cycle_index = cycle_index + 1) begin
      for (lane = 0; lane < K; lane = lane + 1)
        retire_ready[lane] = cycle_index[0] ? lane[0] : 1'b1;
      @(posedge clk); @(negedge clk);
    end

    // Populate all lanes, then hold lane zero for the remainder of this phase.
    retire_ready = '1;
    repeat (3) begin @(posedge clk); @(negedge clk); end
    if (!retire_valid[0]) $fatal(1, "lane zero not populated");
    held_event = retire_event[0];
    held_source = retire_source[0];
    before_progress = other_lane_progress;
    retire_ready = '1;
    retire_ready[0] = 1'b0;
    repeat (96) begin
      @(posedge clk); @(negedge clk);
      if (!retire_valid[0] || retire_event[0] != held_event ||
          retire_source[0] != held_source)
        $fatal(1, "permanently stalled lane changed");
    end
    if (other_lane_progress <= before_progress)
      $fatal(1, "unstalled lanes made no progress");

    // Stop admission, release the held lane, and prove exact drain conservation.
    source_valid = '0;
    retire_ready = '1;
    repeat (K+4) begin @(posedge clk); @(negedge clk); end
    for (lane = 0; lane < K; lane = lane + 1)
      if (retire_valid[lane]) $fatal(1, "lane failed to drain=%0d", lane);
    for (source = 0; source < N; source = source + 1)
      if (accepted[source] != delivered[source])
        $fatal(1, "loss source=%0d accepted=%0d delivered=%0d",
               source, accepted[source], delivered[source]);

    $display("A7_BACKPRESSURE_PASS impl=%s N=%0d K=%0d other_lane_progress=%0d",
             (REFERENCE != 0) ? "replicated" : "prefix",
             N, K, other_lane_progress);
    $finish;
  end
endmodule
