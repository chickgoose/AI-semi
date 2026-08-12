`timescale 1ns/1ps

// Directed through the actual frozen candidate modport and storage-free
// compatibility binding.  This test intentionally uses only uniform sink
// ready, the capability advertised by this common binding.
module a3_k2_common_binding_tb;
  localparam int NUM_SOURCES = 16;
  localparam int ADDR_WIDTH = 16;
  localparam int RETIRE_LANES = 2;

  logic clk = 1'b0;
  aer_bench_if #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(RETIRE_LANES)
  ) bench(clk);

  aer_legacy_candidate_adapter #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(RETIRE_LANES),
`ifdef A3_K2_TEST_FIFO_DEPTH_NONZERO
    .FIFO_DEPTH(1)
`else
    .FIFO_DEPTH(0)
`endif
  ) dut(bench);

  integer source;
  always #5 clk = ~clk;

  task automatic clear_sources;
    begin
      bench.source_valid = '0;
      for (source = 0; source < NUM_SOURCES; source = source + 1)
        bench.source_event[source] = '0;
    end
  endtask

  task automatic check_event_matches_source(input integer lane);
    begin
      case (bench.retire_source[lane])
        4'd5: if (bench.retire_event[lane] != 16'hc205)
          $fatal(1, "source5/event mismatch lane=%0d", lane);
        4'd6: if (bench.retire_event[lane] != 16'hf406)
          $fatal(1, "source6 retrigger/event mismatch lane=%0d", lane);
        4'd7: if (bench.retire_event[lane] != 16'hf507)
          $fatal(1, "source7/event mismatch lane=%0d", lane);
        4'd10: if (bench.retire_event[lane] != 16'hd20a)
          $fatal(1, "source10/event mismatch lane=%0d", lane);
        default: $fatal(1, "unexpected normalized source=%0d lane=%0d",
                        bench.retire_source[lane], lane);
      endcase
    end
  endtask

  initial begin
    bench.rst_n = 1'b0;
    bench.retire_ready = 2'b00;
    clear_sources();
    repeat (3) @(posedge clk);

`ifdef A3_K2_TEST_NONUNIFORM_READY
    // Negative capability test: the common seam must fail closed rather than
    // silently claiming independent lane-ready support.
    @(negedge clk);
    bench.rst_n = 1'b1;
    bench.retire_ready = 2'b01;
    @(posedge clk);
    #1;
    $fatal(1, "A3_K2_COMMON nonuniform-ready guard escaped");
`else
    // Register and atomically accept the first ordered count-two offer.
    @(negedge clk);
    bench.rst_n = 1'b1;
    bench.source_valid[4] = 1'b1;
    bench.source_valid[11] = 1'b1;
    bench.source_event[4] = 16'ha104;
    bench.source_event[11] = 16'hb10b;
    @(posedge clk);
    #1;
    if (bench.source_ready != 16'h0810)
      $fatal(1, "count2 source_ready was not exact: %h",
             bench.source_ready);
    @(posedge clk);
    #1;
    if (bench.retire_valid != 2'b01 || bench.retire_source[0] != 4'd4 ||
        bench.retire_event[0] != 16'ha104)
      $fatal(1, "first atomic owner bundle was not charged");

    // A second count-two offer stalls behind the full adapter.  Add source 6
    // only after that owner offer is already held; it must remain pending and
    // must not perturb the held offer or the stalled retire head.
    @(negedge clk);
    clear_sources();
    bench.source_valid[5] = 1'b1;
    bench.source_valid[10] = 1'b1;
    bench.source_event[5] = 16'hc205;
    bench.source_event[10] = 16'hd20a;
    @(posedge clk);
    #1;
    if (bench.source_ready != '0)
      $fatal(1, "full adapter accepted a held count2 offer");
    @(negedge clk);
    bench.source_valid[6] = 1'b1;
    bench.source_event[6] = 16'he306;
    repeat (2) begin
      @(posedge clk);
      #1;
      if (bench.source_ready != '0 || bench.retire_valid != 2'b01 ||
          bench.retire_source[0] != 4'd4 ||
          bench.retire_event[0] != 16'ha104)
        $fatal(1, "new pending source perturbed a stalled owner/link");
    end

    // Uniform drain creates two slots and atomically accepts exactly 5/10.
    // The stalled-new source 6 becomes the next count-one owner offer.
    @(negedge clk);
    bench.retire_ready = 2'b11;
    #1;
    if (bench.retire_valid != 2'b11 || bench.source_ready != 16'h0420)
      $fatal(1, "uniform drain/count2 atomic refill mismatch");
    @(posedge clk);
    #1;
    if (bench.retire_valid != 2'b11 || bench.source_ready != 16'h0040)
      $fatal(1, "stalled-new pending source did not coexist/refill");
    check_event_matches_source(0);
    check_event_matches_source(1);

    // The 5/10 pair retires while source 6 is accepted as a singleton.
    @(negedge clk);
    bench.source_valid[5] = 1'b0;
    bench.source_valid[10] = 1'b0;
    @(posedge clk);
    #1;
    if (bench.retire_valid != 2'b01 || bench.retire_source[0] != 4'd6 ||
        bench.retire_event[0] != 16'he306)
      $fatal(1, "stalled-new singleton did not follow older pair");

    // Clear the accepted old occurrence, then present a same-address source-6
    // retrigger together with source 7 while the old source-6 event is still
    // charged.  The old event drains first; the new pair is accepted later.
    @(negedge clk);
    clear_sources();
    bench.source_valid[6] = 1'b1;
    bench.source_valid[7] = 1'b1;
    bench.source_event[6] = 16'hf406;
    bench.source_event[7] = 16'hf507;
    @(posedge clk);
    #1;
    if (bench.source_ready != 16'h00c0)
      $fatal(1, "retrigger pair was not registered after old occurrence drain");
    @(posedge clk);
    #1;
    if (bench.retire_valid != 2'b11)
      $fatal(1, "retrigger coexistence pair was not charged");
    check_event_matches_source(0);
    check_event_matches_source(1);

    // Preserve accepted event identities after common source-latch clear.
    @(negedge clk);
    clear_sources();
    bench.retire_ready = 2'b00;
    repeat (2) begin
      @(posedge clk);
      #1;
      if (bench.retire_valid != 2'b01)
        $fatal(1, "uniform stall lost the accepted retrigger head");
      check_event_matches_source(0);
    end

    // Mid-stall reset flushes both owner and charged adapter without phantom.
    @(negedge clk);
    bench.rst_n = 1'b0;
    @(posedge clk);
    #1;
    if (bench.source_ready != '0 || bench.retire_valid != 2'b00)
      $fatal(1, "reset did not quiet the candidate modport");
    @(negedge clk);
    bench.rst_n = 1'b1;
    bench.retire_ready = 2'b11;
    repeat (3) begin
      @(posedge clk);
      #1;
      if (bench.retire_valid != 2'b00 || bench.source_ready != '0)
        $fatal(1, "phantom handshake after reset/drain");
    end

    $display("A3_K2_COMMON_BINDING_PASS");
    $finish;
`endif
  end
endmodule
