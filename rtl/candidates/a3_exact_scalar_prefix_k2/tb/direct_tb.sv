`timescale 1ns/1ps

module a3_exact_scalar_prefix_k2_direct_tb;
  logic clk = 1'b0;
  logic rst;
  logic [15:0] source_pending;
  logic bundle_ready;
  wire [1:0] grant_count;
  wire [3:0] lane0_addr;
  wire [3:0] lane1_addr;

  integer row_count [0:3];
  integer bundles;
  integer cycles;
  reg [1:0] hold_count;
  reg [3:0] hold_a0;
  reg [3:0] hold_a1;
  reg [2:0] hold_round;
  reg [2:0] hold_center;
  reg [2:0] hold_periph;
  reg [2:0] hold_column;

  always #5 clk = ~clk;

  a3_exact_scalar_prefix_k2 dut (
    .clk(clk), .rst(rst), .source_pending(source_pending),
    .grant_count(grant_count), .lane0_addr(lane0_addr),
    .lane1_addr(lane1_addr),
    .bundle_ready(bundle_ready)
  );

  task automatic drive_edge(input logic [15:0] req, input logic ready);
    begin
      @(negedge clk);
      source_pending = req;
      bundle_ready = ready;
      @(posedge clk);
      #1;
    end
  endtask

  task automatic reset_dut;
    begin
      @(negedge clk);
      rst = 1'b1;
      source_pending = 16'hffff;
      bundle_ready = 1'b0;
      @(posedge clk);
      #1;
      if (grant_count != 0)
        $fatal(1, "DIRECT reset left valid output");
      if (dut.round_state !== 3'd0 || dut.center_state !== 3'b111 ||
          dut.periph_state !== 3'b111 || dut.column_state !== 3'b111)
        $fatal(1, "DIRECT reset state mismatch");
      @(negedge clk);
      rst = 1'b0;
    end
  endtask

  initial begin
    rst = 1'b1;
    source_pending = 16'b0;
    bundle_ready = 1'b0;

    // Persistent opportunity ratio: count 120 committed full K2 bundles.
    reset_dut();
    row_count[0] = 0; row_count[1] = 0;
    row_count[2] = 0; row_count[3] = 0;
    bundles = 0;
    cycles = 0;
    while (bundles < 120 && cycles < 130) begin
      drive_edge(16'hffff, 1'b1);
      // Values now visible will commit at the next ready edge.  Count the
      // bundle that committed on this edge from the prior visible outputs by
      // skipping the initial fill and counting after every subsequent edge.
      if (cycles > 0) begin
        if (grant_count != 2)
          $fatal(1, "DIRECT persistent bundle not full cycle=%0d", cycles);
        // With ready continuously high, the newly visible bundle is also the
        // next bundle; counting 120 consecutive bundles is phase-equivalent.
        row_count[lane0_addr[3:2]] = row_count[lane0_addr[3:2]] + 1;
        row_count[lane1_addr[3:2]] = row_count[lane1_addr[3:2]] + 1;
        bundles = bundles + 1;
      end
      cycles = cycles + 1;
    end
    if (bundles != 120 || row_count[0] != 20 || row_count[1] != 100 ||
        row_count[2] != 100 || row_count[3] != 20)
      $fatal(1, "DIRECT persistent ratio mismatch bundles=%0d rows=%0d,%0d,%0d,%0d",
             bundles, row_count[0], row_count[1], row_count[2], row_count[3]);
    $display("A3_K2_PERSISTENT_PASS rows=%0d,%0d,%0d,%0d bundles=%0d",
             row_count[0], row_count[1], row_count[2], row_count[3], bundles);

    // Sparse fallback: no center request while round prefers center.  Exact
    // scalar folding must issue the two peripheral addresses in order.
    reset_dut();
    drive_edge(16'h1001, 1'b1);
    if (!(grant_count == 2 && lane0_addr == 4'd0 && lane1_addr == 4'd12))
      $fatal(1, "DIRECT sparse peripheral fallback mismatch g0=%0d/%0d g1=%0d/%0d",
             grant_count >= 1, lane0_addr, grant_count == 2, lane1_addr);
    $display("A3_K2_SPARSE_FALLBACK_PASS");

    // Stall: adding unrelated pending bits must not perturb the held bundle or
    // committed policy state.  Atomic ready releases both lanes together.
    hold_count = grant_count; hold_a0 = lane0_addr; hold_a1 = lane1_addr;
    hold_round = dut.round_state; hold_center = dut.center_state;
    hold_periph = dut.periph_state; hold_column = dut.column_state;
    repeat (4) begin
      drive_edge(16'hf11f, 1'b0);
      if (grant_count !== hold_count || lane0_addr !== hold_a0 ||
          lane1_addr !== hold_a1)
        $fatal(1, "DIRECT stalled bundle changed");
      if (dut.round_state !== hold_round || dut.center_state !== hold_center ||
          dut.periph_state !== hold_periph || dut.column_state !== hold_column)
        $fatal(1, "DIRECT state advanced under stall");
    end
    drive_edge(16'hf11f, 1'b1);
    if (dut.round_state == hold_round)
      $fatal(1, "DIRECT atomic commit did not advance policy state");
    $display("A3_K2_STALL_ATOMIC_PASS");

    // Reset while stalled must discard the reservation and restore all state.
    bundle_ready = 1'b0;
    @(negedge clk); rst = 1'b1;
    @(posedge clk); #1;
    if (grant_count != 0 || dut.round_state != 0)
      $fatal(1, "DIRECT mid-stall reset failed");
    @(negedge clk); rst = 1'b0; source_pending = 16'b0;
    @(posedge clk); #1;
    if (grant_count != 0)
      $fatal(1, "DIRECT stale output after reset drain");
    $display("A3_K2_RESET_DRAIN_PASS");

    // Same address may be requested again only after the prior pending bit is
    // cleared.  It must not replay while absent and must reappear after rearm.
    drive_edge(16'h0020, 1'b1);
    if (!(grant_count == 1 && lane0_addr == 4'd5))
      $fatal(1, "DIRECT first single request mismatch");
    drive_edge(16'h0020, 1'b1); // commit; refill masks the committed address
    if (grant_count != 0)
      $fatal(1, "DIRECT single address replayed without retrigger");
    drive_edge(16'h0000, 1'b1);
    drive_edge(16'h0020, 1'b1);
    if (!(grant_count == 1 && lane0_addr == 4'd5))
      $fatal(1, "DIRECT rearmed request was not emitted");
    $display("A3_K2_RETRIGGER_PASS");

    $display("A3_EXACT_SCALAR_PREFIX_K2_DIRECT_PASS");
    $finish;
  end
endmodule
