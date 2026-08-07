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
  logic [N-1:0][AW-1:0] source_event;
  logic [N-1:0] source_ready;
  logic [K-1:0] retire_valid;
  logic [K-1:0][AW-1:0] retire_event;
  logic [K-1:0][SW-1:0] retire_source;
  logic [K-1:0] retire_ready;

  logic [N-1:0] prefix_request;
  logic [N-1:0][CW-1:0] prefix_count;
  logic [CW-1:0] prefix_total;
  logic [N-1:0][CW-1:0] segmented_count;
  logic [CW-1:0] segmented_total;
  logic [SW-1:0] select_rotation;
  logic [K-1:0] exhaustive_selected_valid;
  logic [K-1:0][SW-1:0] exhaustive_selected_index;
  logic [N-1:0] exhaustive_selected_onehot;
  logic [N-1:0] segmented_source_ready;
  logic [K-1:0] segmented_retire_valid;
  logic [K-1:0][AW-1:0] segmented_retire_event;
  logic [K-1:0][SW-1:0] segmented_retire_source;
  integer mask;
  integer source;
  integer lane;
  integer rotation;
  integer offset;
  integer physical;
  integer expected_slot;
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

  a7_radix4_segmented_prefix_count #(.NUM_SOURCES(N)) segmented_prefix_dut (
    .request(prefix_request), .inclusive_count(segmented_count),
    .total_count(segmented_total)
  );

  a7_shared_rank_index_select #(
    .NUM_SOURCES(N), .SELECT_LANES(K)
  ) exhaustive_index_dut (
    .request(prefix_request), .inclusive_count(segmented_count),
    .total_count(segmented_total), .rotation_base(select_rotation),
    .select_limit(CW'(K)), .selected_valid(exhaustive_selected_valid),
    .selected_index(exhaustive_selected_index),
    .selected_onehot(exhaustive_selected_onehot)
  );

  a7_parallel_event_compactor #(
    .NUM_SOURCES(N), .ADDR_WIDTH(AW), .RETIRE_LANES(K)
  ) dut (
    .clk, .rst_n, .source_valid, .source_event, .source_ready,
    .retire_valid, .retire_event, .retire_source, .retire_ready
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

  task automatic check_segmented_equivalence;
    begin
      if (segmented_source_ready !== source_ready)
        $fatal(1, "segmented source_ready mismatch K=%0d", K);
      if (segmented_retire_valid !== retire_valid)
        $fatal(1, "segmented retire_valid mismatch K=%0d", K);
      for (lane = 0; lane < K; lane = lane + 1)
        if (retire_valid[lane] &&
            ((segmented_retire_event[lane] !== retire_event[lane]) ||
             (segmented_retire_source[lane] !== retire_source[lane])))
          $fatal(1, "segmented retire payload mismatch lane=%0d K=%0d", lane, K);
    end
  endtask

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
    select_rotation = '0;
    for (mask = 0; mask < (1 << N); mask = mask + 1) begin
      prefix_request = N'(mask);
      #1;
      expected = 0;
      for (source = 0; source < N; source = source + 1) begin
        expected = expected + prefix_request[source];
        if (prefix_count[source] != expected)
          $fatal(1, "prefix mismatch mask=%0h source=%0d got=%0d expected=%0d",
                 mask, source, prefix_count[source], expected);
        if (segmented_count[source] != prefix_count[source])
          $fatal(1, "segmented prefix mismatch mask=%0h source=%0d", mask, source);
      end
      if (prefix_total != expected) $fatal(1, "total mismatch mask=%0h", mask);
      if (segmented_total != prefix_total)
        $fatal(1, "segmented total mismatch mask=%0h", mask);
      for (rotation = 0; rotation < N; rotation = rotation + 1) begin
        select_rotation = SW'(rotation);
        #1;
        expected_slot = 0;
        for (offset = 0; offset < N; offset = offset + 1) begin
          physical = rotation + offset;
          if (physical >= N) physical = physical - N;
          if (prefix_request[physical]) begin
            if (expected_slot < K) begin
              if (!exhaustive_selected_valid[expected_slot] ||
                  (exhaustive_selected_index[expected_slot] != SW'(physical)))
                $fatal(1, "index mismatch mask=%0h rotation=%0d slot=%0d",
                       mask, rotation, expected_slot);
            end
            expected_slot = expected_slot + 1;
          end
        end
        for (lane = 0; lane < K; lane = lane + 1)
          if (exhaustive_selected_valid[lane] != (lane < expected_slot))
            $fatal(1, "index valid mismatch mask=%0h rotation=%0d slot=%0d",
                   mask, rotation, lane);
      end
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
      check_segmented_equivalence();
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
      check_segmented_equivalence();
      if (!retire_valid[0] || retire_event[0] != held_event ||
          retire_source[0] != held_source)
        $fatal(1, "stalled lane changed K=%0d", K);
      for (lane = 1; lane < K; lane = lane + 1)
        if (retire_valid[lane] && (retire_source[lane] == held_source))
          $fatal(1, "inflight source duplicated across lanes source=%0d", held_source);
    end
    retire_ready = '1;
    repeat (3) @(posedge clk);
    $display("A7_UNIT_PASS N=%0d K=%0d exhaustive_masks=%0d rotations=%0d",
             N, K, (1 << N), N);
    $finish;
  end
endmodule
