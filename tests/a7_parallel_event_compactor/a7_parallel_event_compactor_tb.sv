`timescale 1ns/1ps

module a7_parallel_event_compactor_tb;
  parameter int N = 16;
  parameter int K = 4;
  localparam int AW = 8;
  localparam int SW = $clog2(N);
  localparam int CW = $clog2(N+1);

  logic clk = 0;
  always #5 clk = ~clk;
  logic rst_n;
  logic [N-1:0] source_valid;
  logic [AW-1:0] source_event [N];
  logic [N-1:0] source_ready;
  logic [K-1:0] retire_valid;
  logic [AW-1:0] retire_event [K];
  logic [SW-1:0] retire_source [K];
  logic [K-1:0] retire_ready;

  logic [N-1:0] prefix_request;
  logic [CW-1:0] prefix_count [N];
  logic [CW-1:0] prefix_total;
  integer mask;
  integer source;
  integer lane;
  integer expected;
  integer grants;
  integer cycles_since_service [N];
  logic [N-1:0] granted_sources;
  logic [AW-1:0] held_event;
  logic [SW-1:0] held_source;

  a7_parallel_prefix_count #(.NUM_SOURCES(N)) prefix_dut (
    .request(prefix_request), .inclusive_count(prefix_count),
    .total_count(prefix_total)
  );

  a7_parallel_event_compactor #(
    .NUM_SOURCES(N), .ADDR_WIDTH(AW), .RETIRE_LANES(K)
  ) dut (
    .clk, .rst_n, .source_valid, .source_event, .source_ready,
    .retire_valid, .retire_event, .retire_source, .retire_ready
  );

  task automatic check_grants;
    begin
      grants = 0;
      granted_sources = '0;
      for (source = 0; source < N; source = source + 1) begin
        if (source_ready[source]) begin
          if (granted_sources[source]) $fatal(1, "duplicate source grant %0d", source);
          granted_sources[source] = 1'b1;
          grants = grants + 1;
          cycles_since_service[source] = 0;
        end else begin
          cycles_since_service[source] = cycles_since_service[source] + 1;
        end
        if (cycles_since_service[source] > ((N+K-1)/K)+1)
          $fatal(1, "fairness bound source=%0d wait=%0d K=%0d",
                 source, cycles_since_service[source], K);
      end
      if (grants > K) $fatal(1, "too many grants=%0d K=%0d", grants, K);
    end
  endtask

  initial begin
    // Exhaust all N=16 bitmap values and validate every shared prefix output.
    prefix_request = '0;
    for (mask = 0; mask < (1 << N); mask = mask + 1) begin
      prefix_request = N'(mask);
      #1;
      expected = 0;
      for (source = 0; source < N; source = source + 1) begin
        expected = expected + prefix_request[source];
        if (prefix_count[source] != expected)
          $fatal(1, "prefix mismatch mask=%0h source=%0d got=%0d expected=%0d",
                 mask, source, prefix_count[source], expected);
      end
      if (prefix_total != expected) $fatal(1, "total mismatch mask=%0h", mask);
    end

    rst_n = 0;
    source_valid = '0;
    retire_ready = '1;
    for (source = 0; source < N; source = source + 1) begin
      source_event[source] = AW'(source);
      cycles_since_service[source] = 0;
    end
    repeat (2) @(posedge clk);
    rst_n = 1;
    source_valid = '1;

    // Persistent contention checks K limit, uniqueness, refill, and RR bound.
    repeat (3*((N+K-1)/K)+3) begin
      @(negedge clk);
      check_grants();
    end

    // Hold lane zero while other lanes continue. Its registered output must be stable.
    @(negedge clk);
    retire_ready = '1;
    retire_ready[0] = 1'b0;
    if (!retire_valid[0]) $fatal(1, "lane zero not populated before stall");
    held_event = retire_event[0];
    held_source = retire_source[0];
    repeat (6) begin
      @(negedge clk);
      if (!retire_valid[0] || retire_event[0] != held_event ||
          retire_source[0] != held_source)
        $fatal(1, "stalled lane changed K=%0d", K);
      for (lane = 1; lane < K; lane = lane + 1)
        if (retire_valid[lane] && (retire_source[lane] == held_source))
          $fatal(1, "inflight source duplicated across lanes source=%0d", held_source);
    end
    retire_ready = '1;
    repeat (3) @(posedge clk);
    $display("A7_UNIT_PASS N=%0d K=%0d exhaustive_masks=%0d", N, K, (1 << N));
    $finish;
  end
endmodule
