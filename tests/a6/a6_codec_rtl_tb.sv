`timescale 1ns/1ps

module a6_codec_rtl_tb;
  localparam int NUM_SOURCES = 16;
  localparam int EVENT_WIDTH = 6;
  logic clk = 1'b0;
  logic rst_n = 1'b0;
  logic [NUM_SOURCES-1:0] source_valid;
  logic [NUM_SOURCES-1:0] source_ready;
  logic retire_valid;
  logic retire_ready;
  logic [EVENT_WIDTH-1:0] retire_event;
  logic [3:0] retire_source;
  logic [1:0] link_count;
  logic [1:0] link_data;
  logic link_ready;
  integer expected [0:255];
  integer accepted_count;
  integer retired_count;
  integer source;
  integer cycle;

  always #5 clk = ~clk;

  a6_lossless_aer_codec_top dut (
    .clk(clk), .rst_n(rst_n),
    .source_valid(source_valid), .source_ready(source_ready),
    .retire_valid(retire_valid), .retire_ready(retire_ready),
    .retire_event(retire_event), .retire_source(retire_source),
    .link_count_observe(link_count), .link_data_observe(link_data),
    .link_ready_observe(link_ready)
  );

  always @(posedge clk) begin
    for (source = 0; source < NUM_SOURCES; source = source + 1) begin
      if (source_valid[source] && source_ready[source]) begin
        expected[accepted_count] = source;
        accepted_count = accepted_count + 1;
      end
    end
    if (retire_valid && retire_ready) begin
      if (retired_count >= accepted_count)
        $fatal(1, "phantom retire source=%0d", retire_source);
      if (retire_source !== expected[retired_count])
        $fatal(1, "order/address mismatch index=%0d got=%0d expected=%0d",
               retired_count, retire_source, expected[retired_count]);
      if (retire_event !== {retire_source, 2'b10})
        $fatal(1, "event reconstruction mismatch got=%0h", retire_event);
      retired_count = retired_count + 1;
    end
  end

  task automatic hold_source(input integer source_index, input integer handshakes);
    integer start_count;
    begin
      start_count = accepted_count;
      source_valid[source_index] = 1'b1;
      while (accepted_count < start_count + handshakes)
        @(negedge clk);
      source_valid[source_index] = 1'b0;
    end
  endtask

  task automatic pulse_mask(input logic [NUM_SOURCES-1:0] mask);
    begin
      source_valid = mask;
      @(negedge clk);
      while ((source_valid & source_ready) == '0)
        @(negedge clk);
      source_valid = '0;
    end
  endtask

  initial begin
    source_valid = '0;
    retire_ready = 1'b1;
    accepted_count = 0;
    retired_count = 0;
    repeat (3) @(negedge clk);
    rst_n = 1'b1;

    // RAW, exact nine-occurrence RUN, local deltas, and nonlocal RAW escape.
    hold_source(4, 9);
    pulse_mask(16'h0020);
    pulse_mask(16'h0010);
    pulse_mask(16'h8000);

    // Exercise fair selection and decoder output stability under backpressure.
    source_valid = 16'h000f;
    repeat (8) @(negedge clk);
    source_valid = '0;
    retire_ready = 1'b0;
    repeat (4) @(negedge clk);
    retire_ready = 1'b1;

    for (cycle = 0; cycle < 1000 && retired_count < accepted_count;
         cycle = cycle + 1)
      @(negedge clk);
    if (retired_count != accepted_count)
      $fatal(1, "drain mismatch accepted=%0d retired=%0d",
             accepted_count, retired_count);

    // Reset must discard all history and suppress phantom completion.
    rst_n = 1'b0;
    repeat (2) @(negedge clk);
    rst_n = 1'b1;
    repeat (10) @(negedge clk);
    if (retire_valid)
      $fatal(1, "phantom completion after reset");
    $display("A6_CODEC_RTL_TEST_PASS accepted=%0d retired=%0d",
             accepted_count, retired_count);
    $finish;
  end
endmodule
