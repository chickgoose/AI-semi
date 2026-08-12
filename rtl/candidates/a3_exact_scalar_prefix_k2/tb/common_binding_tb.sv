`timescale 1ns/1ps

module a3_k2_common_binding_tb;
  localparam int NUM_SOURCES = 16;
  localparam int ADDR_WIDTH = 16;
  localparam int RETIRE_LANES = 2;

  logic clk = 1'b0;
  logic rst_n;
  logic [NUM_SOURCES-1:0] source_valid;
  logic [NUM_SOURCES-1:0] source_ready;
  logic [ADDR_WIDTH-1:0] source_event [NUM_SOURCES];
  logic [RETIRE_LANES-1:0] retire_valid;
  logic [RETIRE_LANES-1:0] retire_ready;
  logic [ADDR_WIDTH-1:0] retire_event [RETIRE_LANES];
  logic [3:0] retire_source [RETIRE_LANES];

  a3_k2_common_wrapper #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(4)
  ) dut(.*);

  integer source;
  always #5 clk = ~clk;

  task automatic clear_sources;
    begin
      source_valid = '0;
      for (source = 0; source < NUM_SOURCES; source = source + 1)
        source_event[source] = '0;
    end
  endtask

  initial begin
    rst_n = 1'b0;
    retire_ready = 2'b00;
    clear_sources();
    repeat (3) @(posedge clk);

    // The first owner offer is registered, then atomically accepted on the
    // following edge.  No address outside the complete count=2 bundle may be
    // reported ready.
    @(negedge clk);
    rst_n = 1'b1;
    source_valid[4] = 1'b1;
    source_valid[11] = 1'b1;
    source_event[4] = 16'ha104;
    source_event[11] = 16'hb10b;
    @(posedge clk);
    #1;
    if (source_ready != 16'h0810)
      $fatal(1, "count=2 source_ready was not the exact owner bundle: %h",
             source_ready);
    @(posedge clk);
    #1;
    if (retire_valid != 2'b01 || retire_source[0] != 4'd4 ||
        retire_event[0] != 16'ha104)
      $fatal(1, "accepted owner bundle did not enter charged adapter");

    // Present a second full owner offer while the adapter is full.  A ready
    // younger lane alone cannot retire or create capacity, and source_ready
    // must remain zero rather than partially accepting count=2.
    @(negedge clk);
    clear_sources();
    source_valid[5] = 1'b1;
    source_valid[10] = 1'b1;
    source_event[5] = 16'hc205;
    source_event[10] = 16'hd20a;
    retire_ready = 2'b10;
    @(posedge clk);
    #1;
    if (source_ready != 16'h0000 || retire_valid != 2'b01 ||
        retire_source[0] != 4'd4 || retire_event[0] != 16'ha104)
      $fatal(1, "full-link stall corrupted acceptance or ordered head");
    repeat (2) begin
      @(posedge clk);
      #1;
      if (source_ready != 16'h0000 ||
          retire_source[0] != 4'd4 || retire_event[0] != 16'ha104)
        $fatal(1, "common output changed under continuous head stall");
    end

    // One head retirement leaves one slot, still insufficient for count=2.
    // On the next edge the old younger entry retires and both new sources are
    // atomically admitted into the capacity freed by that edge.
    @(negedge clk);
    retire_ready = 2'b01;
    #1;
    if (source_ready != 16'h0000)
      $fatal(1, "count=2 offer partially fit into one slot");
    @(posedge clk);
    #1;
    if (retire_source[0] != 4'd11 ||
        retire_event[0] != 16'hb10b ||
        source_ready != 16'h0420)
      $fatal(1, "compaction/refill acceptance boundary mismatch");
    @(posedge clk);
    #1;
    if (retire_source[0] != 4'd5 ||
        retire_event[0] != 16'hc205)
      $fatal(1, "atomic refill did not preserve scheduler order/payload");

    // Mimic common source-latch clearing, then stall the newly buffered pair.
    @(negedge clk);
    clear_sources();
    retire_ready = 2'b00;
    repeat (2) begin
      @(posedge clk);
      #1;
      if (retire_valid != 2'b01 || retire_source[0] != 4'd5 ||
          retire_event[0] != 16'hc205)
        $fatal(1, "accepted payload was not held independently of source pins");
    end

    // Mid-stall reset flushes owner and adapter state.  Outputs are quiet and
    // no accepted pre-reset event may appear after release.
    @(negedge clk);
    rst_n = 1'b0;
    @(posedge clk);
    #1;
    if (source_ready != '0 || retire_valid != 2'b00)
      $fatal(1, "reset did not quiet common boundary");
    @(negedge clk);
    rst_n = 1'b1;
    source_valid[6] = 1'b1;
    source_event[6] = 16'he306;
    @(posedge clk);
    #1;
    if (source_ready != 16'h0040)
      $fatal(1, "single-source ready was not exact after reset");
    @(posedge clk);
    #1;
    if (retire_valid != 2'b01 || retire_source[0] != 4'd6 ||
        retire_event[0] != 16'he306)
      $fatal(1, "post-reset accepted event was not buffered");

    // Change the upstream pins after acceptance; the charged adapter must
    // retain the accepted event until the sink finally drains it.
    @(negedge clk);
    clear_sources();
    source_event[6] = 16'hffff;
    repeat (2) @(posedge clk);
    #1;
    if (retire_event[0] != 16'he306)
      $fatal(1, "accepted event changed with upstream source_event");
    @(negedge clk);
    retire_ready = 2'b11;
    @(posedge clk);
    #1;
    if (retire_valid != 2'b00)
      $fatal(1, "final event failed to drain");
    repeat (3) begin
      @(posedge clk);
      #1;
      if (retire_valid != 2'b00)
        $fatal(1, "phantom completion after drain");
    end

    $display("A3_K2_COMMON_BINDING_PASS");
    $finish;
  end
endmodule
