`timescale 1ns/1ps
// dual_level_arbiter_tb.sv
//
// Standalone self-checking testbench for dual_level_arbiter (the
// 256-source, 16-groups-of-16 scale-up design). Tests the arbiter in
// isolation (req/advance/grant only) rather than through the full AER
// pipeline, since fairness/starvation is purely a property of this
// block and doesn't need the TX stage to exercise.

module dual_level_arbiter_tb;

  localparam int NUM_SOURCES = 256;
  localparam int GROUP_SIZE  = 16;
  localparam int NUM_GROUPS  = NUM_SOURCES / GROUP_SIZE;

  logic clk = 0;
  always #5 clk = ~clk;

  logic rst_n;
  logic [NUM_SOURCES-1:0] req;
  logic                   advance;
  logic [NUM_SOURCES-1:0] grant;

  dual_level_arbiter #(
      .NUM_SOURCES(NUM_SOURCES),
      .GROUP_SIZE (GROUP_SIZE)
  ) dut (
      .clk     (clk),
      .rst_n   (rst_n),
      .req     (req),
      .advance (advance),
      .grant   (grant)
  );

  longint serviced[NUM_SOURCES];
  int total_error_cnt;

  task automatic do_reset;
    rst_n = 1'b0;
    req   = '0;
    advance = 1'b0;
    for (int i = 0; i < NUM_SOURCES; i++) serviced[i] = 0;
    repeat (4) @(posedge clk);
    rst_n = 1'b1;
    @(posedge clk);
  endtask

  // Every cycle: whatever is granted (if anything) is immediately
  // consumed (advance follows grant combinationally, like a downstream
  // that's always ready) -- maximizes contention pressure per cycle.
  always_comb advance = |grant;

  function automatic int grant_index();
    for (int i = 0; i < NUM_SOURCES; i++) if (grant[i]) return i;
    return -1;
  endfunction

  task automatic run_cycles(int n);
    int idx;
    for (int c = 0; c < n; c++) begin
      @(posedge clk);
      idx = grant_index();
      if (idx >= 0) serviced[idx]++;
    end
  endtask

  // tol: max acceptable (max-min) spread among the given sources. The
  // flat single-level arbiter gives EXACT equality when contenders sit
  // in equally-sized groups (tol=2 is generous already). But a
  // hierarchical arbiter only gives exact per-GROUP fairness -- when
  // group occupancy is non-uniform (e.g. one group has 15 active
  // members because one of its 16 sources is permanently silent, while
  // every other group has all 16 active), sources in the
  // lighter-loaded group get slightly more turns per capita than
  // sources in a fully-loaded group. This is bounded, not starvation
  // (nobody is starved -- everybody still gets serviced every round),
  // but it is measurably less exact than the flat design, so callers
  // for non-uniform scenarios must pass a wider tol explicitly instead
  // of silently reusing the flat design's tight bound.
  task automatic check_fair(string name, int idx_list[], int tol);
    longint mn, mx;
    bit first;
    mn = 0; mx = 0; first = 1'b1;
    foreach (idx_list[i]) begin
      if (first) begin mn = serviced[idx_list[i]]; mx = serviced[idx_list[i]]; first = 1'b0; end
      else begin
        if (serviced[idx_list[i]] < mn) mn = serviced[idx_list[i]];
        if (serviced[idx_list[i]] > mx) mx = serviced[idx_list[i]];
      end
    end
    $display("  [%s] serviced min=%0d max=%0d (tol=%0d)", name, mn, mx, tol);
    if (mx - mn > tol) begin
      total_error_cnt++;
      $error("%s: unfair split among active sources (min=%0d max=%0d tol=%0d)", name, mn, mx, tol);
    end
    foreach (idx_list[i]) if (serviced[idx_list[i]] == 0 && mx > 0) begin
      total_error_cnt++;
      $error("%s: source %0d starved (0 serviced while others got up to %0d)", name, idx_list[i], mx);
    end
  endtask

  // ---- Test 1: two sources in the SAME group contend ----
  task automatic test_same_group;
    int idxs[] = '{0, 1};  // both in group 0
    do_reset();
    req = '0; req[0] = 1'b1; req[1] = 1'b1;
    run_cycles(400);
    req = '0;
    $display("---- test_same_group ----");
    check_fair("same_group", idxs, 2);
  endtask

  // ---- Test 2: two sources in DIFFERENT groups contend ----
  task automatic test_diff_group;
    int idxs[] = '{0, 200};  // group 0 vs group 12
    do_reset();
    req = '0; req[0] = 1'b1; req[200] = 1'b1;
    run_cycles(400);
    req = '0;
    $display("---- test_diff_group ----");
    check_fair("diff_group", idxs, 2);
  endtask

  // ---- Test 3: fully saturated -- all 256 sources always requesting ----
  task automatic test_saturated_all;
    int idxs[NUM_SOURCES];
    do_reset();
    req = '1;
    run_cycles(NUM_SOURCES * 20);
    req = '0;
    $display("---- test_saturated_all ----");
    for (int i = 0; i < NUM_SOURCES; i++) idxs[i] = i;
    check_fair("saturated_all", idxs, 2);
  endtask

  // ---- Test 4: 255 active saturated, 1 permanently silent ----
  task automatic test_all_but_one;
    int silent_idx;
    int idxs[NUM_SOURCES-1];
    int k;
    silent_idx = 130;  // middle of group 8
    do_reset();
    req = '1;
    req[silent_idx] = 1'b0;
    run_cycles((NUM_SOURCES - 1) * 20);
    req = '0;
    $display("---- test_all_but_one (silent_idx=%0d) ----", silent_idx);
    if (serviced[silent_idx] != 0) begin
      total_error_cnt++;
      $error("test_all_but_one: silent source %0d was serviced %0d times (expected 0)",
             silent_idx, serviced[silent_idx]);
    end
    k = 0;
    for (int i = 0; i < NUM_SOURCES; i++) begin
      if (i == silent_idx) continue;
      idxs[k] = i; k++;
    end
    // Non-uniform group occupancy here (silent_idx's group has only 15
    // active members, every other group has 16) -- see check_fair's
    // comment. Measured spread with this exact scenario: min=19 max=22
    // (avg 20) over (NUM_SOURCES-1)*20 cycles. Real, bounded, not
    // starvation; tol=4 catches genuine regressions while allowing for
    // this documented, expected effect.
    check_fair("all_but_one active set", idxs, 4);
  endtask

  // ---- Test 5: skewed load -- group 0 fully saturated (16 active),
  // every other group has exactly ONE active source. This is not a
  // pass/fail fairness check (hierarchical arbiters intentionally give
  // per-GROUP fairness, not per-SOURCE fairness across differently
  // loaded groups -- a lone source in an idle group gets more turns
  // than any one source inside a hot group, by design) -- just reports
  // the numbers so the tradeoff is visible and documented.
  task automatic test_skewed_groups;
    do_reset();
    req = '0;
    for (int i = 0; i < GROUP_SIZE; i++) req[i] = 1'b1;               // group 0: all 16 active
    for (int g = 1; g < NUM_GROUPS; g++) req[g*GROUP_SIZE] = 1'b1;    // every other group: 1 active
    run_cycles(NUM_GROUPS * GROUP_SIZE * 10);
    req = '0;
    begin
      longint g0_sum;
      g0_sum = 0;
      for (int i = 0; i < GROUP_SIZE; i++) g0_sum += serviced[i];
      $display("---- test_skewed_groups (informational, not pass/fail) ----");
      $display("  group0 (16 contenders) per-source avg = %0.2f (sum=%0d / 16)",
                real'(g0_sum) / 16.0, g0_sum);
      $display("  group1 lone source (idx=%0d) serviced=%0d", GROUP_SIZE, serviced[GROUP_SIZE]);
      $display("  group2 lone source (idx=%0d) serviced=%0d", 2*GROUP_SIZE, serviced[2*GROUP_SIZE]);
    end
  endtask

  initial begin
    total_error_cnt = 0;
    test_same_group();
    test_diff_group();
    test_saturated_all();
    test_all_but_one();
    test_skewed_groups();
    if (total_error_cnt == 0) $display("ALL TESTS PASSED");
    else $display("TESTS FAILED: %0d total error(s)", total_error_cnt);
    $finish;
  end

endmodule : dual_level_arbiter_tb
