`timescale 1ns/1ps

module a3_k2_common_semantic_tb;
  logic clk = 1'b0;
  logic pending = 1'b0;
  logic measurement_active = 1'b0;
  integer cycle_count = 0;
  integer generated = 0;
  integer overrun = 0;
  integer accepted = 0;
  integer delivered = 0;
  integer measurement_delivered = 0;
  integer occurrence_cycle = -1;
  integer accept_cycle = -1;
  integer delivery_cycle = -1;
  integer source;

  always #5 clk = ~clk;

  aer_bench_if #(
    .NUM_SOURCES(16), .ADDR_WIDTH(16), .RETIRE_LANES(2)
  ) bench(clk);

  aer_legacy_candidate_adapter #(
    .NUM_SOURCES(16), .ADDR_WIDTH(16), .RETIRE_LANES(2), .FIFO_DEPTH(0)
  ) dut(bench.candidate);

  always_comb begin
    bench.source_valid = '0;
    bench.source_valid[0] = pending;
    for (source = 0; source < 16; source = source + 1)
      bench.source_event[source] = 16'(source);
  end

  task automatic offer_source_zero;
    begin
      generated = generated + 1;
      if (pending) begin
        overrun = overrun + 1;
      end else begin
        pending = 1'b1;
        occurrence_cycle = cycle_count;
      end
    end
  endtask

  always @(posedge clk or negedge bench.rst_n) begin
    if (!bench.rst_n) begin
      cycle_count = 0;
      accepted = 0;
      delivered = 0;
      measurement_delivered = 0;
      pending <= 1'b0;
    end else begin
      cycle_count = cycle_count + 1;
      if (bench.source_valid[0] && bench.source_ready[0]) begin
        if (!pending)
          $fatal(1, "COMMON_SEMANTIC accept did not sample pre-edge pending");
        accepted = accepted + 1;
        accept_cycle = cycle_count;
        pending <= 1'b0;
      end
      if (bench.retire_valid[0] && bench.retire_ready[0]) begin
        delivered = delivered + 1;
        delivery_cycle = cycle_count;
        if (measurement_active)
          measurement_delivered = measurement_delivered + 1;
      end
    end
  end

  initial begin
    bench.rst_n = 1'b0;
    bench.retire_ready = 2'b11;
    repeat (3) @(posedge clk);
    @(negedge clk);
    bench.rst_n = 1'b1;
    measurement_active = 1'b1;

    // Match the common TB's first stimulus wait, then retrigger the source at
    // the negedge immediately before its registered owner offer accepts.
    @(negedge clk);
    offer_source_zero();
    @(negedge clk);
    offer_source_zero();

    // The service edge following the final occurrence remains measured; the
    // adapter retirement one edge later is outside the fixed window.
    @(negedge clk);
    measurement_active = 1'b0;
    repeat (3) @(posedge clk);
    #1;

    if (generated != 2 || overrun != 1 || accepted != 1 || delivered != 1)
      $fatal(1,
        "COMMON_SEMANTIC accounting generated=%0d overrun=%0d accepted=%0d delivered=%0d",
        generated, overrun, accepted, delivered);
    if ((accept_cycle - occurrence_cycle) != 2)
      $fatal(1, "COMMON_SEMANTIC accept latency=%0d",
             accept_cycle - occurrence_cycle);
    if ((delivery_cycle - occurrence_cycle) != 3)
      $fatal(1, "COMMON_SEMANTIC delivery latency=%0d",
             delivery_cycle - occurrence_cycle);
    if (measurement_delivered != 0)
      $fatal(1, "COMMON_SEMANTIC fixed-window delivery escaped");
    if (pending || bench.retire_valid != 2'b00)
      $fatal(1, "COMMON_SEMANTIC did not drain cleanly");

    $display("A3_K2_COMMON_SEMANTIC_PASS generated=2 overrun=1 accepted=1 delivered=1 latency=3");
    $finish;
  end
endmodule
