`timescale 1ns/1ps

module a7_rescue_equivalence_tb;
  parameter int N = 16;
  parameter int K = 2;
  parameter int CYCLES = 2048;
  localparam int AW = 8;
  localparam int SW = $clog2(N);

  logic clk = 0;
  always #5 clk = ~clk;
  logic rst_n;
  logic [N-1:0] source_valid;
  logic [N-1:0][AW-1:0] source_event;
  logic [K-1:0] retire_ready;

  logic [N-1:0] prefix_source_ready;
  logic [K-1:0] prefix_retire_valid;
  logic [K-1:0][AW-1:0] prefix_retire_event;
  logic [K-1:0][SW-1:0] prefix_retire_source;
  logic [N-1:0] segmented_source_ready;
  logic [K-1:0] segmented_retire_valid;
  logic [K-1:0][AW-1:0] segmented_retire_event;
  logic [K-1:0][SW-1:0] segmented_retire_source;
  logic [N-1:0] replicated_source_ready;
  logic [K-1:0] replicated_retire_valid;
  logic [K-1:0][AW-1:0] replicated_retire_event;
  logic [K-1:0][SW-1:0] replicated_retire_source;

  logic [63:0] random_state;
  integer cycle_index;
  integer source;
  integer lane;

  function automatic [63:0] xorshift64(input [63:0] value);
    reg [63:0] next_value;
    begin
      next_value = value ^ (value << 13);
      next_value = next_value ^ (next_value >> 7);
      xorshift64 = next_value ^ (next_value << 17);
    end
  endfunction

  a7_parallel_event_compactor #(
    .NUM_SOURCES(N), .ADDR_WIDTH(AW), .RETIRE_LANES(K)
  ) prefix_dut (
    .clk, .rst_n, .source_valid, .source_event,
    .source_ready(prefix_source_ready), .retire_valid(prefix_retire_valid),
    .retire_event(prefix_retire_event), .retire_source(prefix_retire_source),
    .retire_ready
  );

  a7_radix4_segmented_event_compactor #(
    .NUM_SOURCES(N), .ADDR_WIDTH(AW), .RETIRE_LANES(K)
  ) segmented_dut (
    .clk, .rst_n, .source_valid, .source_event,
    .source_ready(segmented_source_ready),
    .retire_valid(segmented_retire_valid),
    .retire_event(segmented_retire_event),
    .retire_source(segmented_retire_source), .retire_ready
  );

  a7_replicated_selector_reference #(
    .NUM_SOURCES(N), .ADDR_WIDTH(AW), .RETIRE_LANES(K)
  ) replicated_dut (
    .clk, .rst_n, .source_valid, .source_event,
    .source_ready(replicated_source_ready),
    .retire_valid(replicated_retire_valid),
    .retire_event(replicated_retire_event),
    .retire_source(replicated_retire_source), .retire_ready
  );

  task automatic check_outputs;
    begin
      if ((segmented_source_ready !== prefix_source_ready) ||
          (replicated_source_ready !== prefix_source_ready))
        $fatal(1, "source_ready mismatch N=%0d K=%0d cycle=%0d", N, K, cycle_index);
      if ((segmented_retire_valid !== prefix_retire_valid) ||
          (replicated_retire_valid !== prefix_retire_valid))
        $fatal(1, "retire_valid mismatch N=%0d K=%0d cycle=%0d", N, K, cycle_index);
      for (lane = 0; lane < K; lane = lane + 1) begin
        if (prefix_retire_valid[lane] &&
            ((segmented_retire_event[lane] !== prefix_retire_event[lane]) ||
             (segmented_retire_source[lane] !== prefix_retire_source[lane]) ||
             (replicated_retire_event[lane] !== prefix_retire_event[lane]) ||
             (replicated_retire_source[lane] !== prefix_retire_source[lane])))
          $fatal(1, "retire payload mismatch N=%0d K=%0d lane=%0d cycle=%0d",
                 N, K, lane, cycle_index);
      end
    end
  endtask

  initial begin
    rst_n = 0;
    source_valid = '0;
    retire_ready = '0;
    random_state = 64'h9e37_79b9_7f4a_7c15 ^ 64'(N) ^ (64'(K) << 8);
    for (source = 0; source < N; source = source + 1)
      source_event[source] = AW'(source);
    repeat (3) @(posedge clk);
    @(negedge clk);
    rst_n = 1;

    for (cycle_index = 0; cycle_index < CYCLES; cycle_index = cycle_index + 1) begin
      random_state = xorshift64(random_state);
      source_valid = N'(random_state);
      random_state = xorshift64(random_state);
      retire_ready = K'(random_state);
      if ((cycle_index >= 700) && (cycle_index < 900)) begin
        retire_ready = '1;
        retire_ready[0] = 1'b0;
      end else if ((cycle_index >= 1200) && (cycle_index < 1400)) begin
        for (lane = 0; lane < K; lane = lane + 1)
          retire_ready[lane] = cycle_index[0] ? lane[0] : 1'b1;
      end
      @(posedge clk);
      @(negedge clk);
      check_outputs();
    end

    source_valid = '0;
    retire_ready = '1;
    repeat (K+3) begin
      @(posedge clk); @(negedge clk); check_outputs();
    end
    $display("A7_RESCUE_EQUIVALENCE_PASS N=%0d K=%0d cycles=%0d", N, K, CYCLES);
    $finish;
  end
endmodule
