`timescale 1ns/1ps

module aer_baseline_starvation_tb;
  localparam int unsigned NUM_SOURCES = 4;
  localparam int unsigned ADDR_WIDTH = 16;

  logic clk;
  logic rst_n;
  logic [NUM_SOURCES-1:0] in_valid;
  logic [NUM_SOURCES-1:0] in_ready;
  logic [ADDR_WIDTH-1:0] in_addr [NUM_SOURCES];
  logic out_valid;
  logic out_ready;
  logic [ADDR_WIDTH-1:0] out_addr;
  logic [1:0] out_src;

  int unsigned source0_accepts;
  int unsigned source3_accepts;

  aer_dut #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH)
  ) dut (
    .clk(clk),
    .rst_n(rst_n),
    .in_valid(in_valid),
    .in_ready(in_ready),
    .in_addr(in_addr),
    .out_valid(out_valid),
    .out_ready(out_ready),
    .out_addr(out_addr),
    .out_src(out_src)
  );

  always #5 clk = ~clk;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      source0_accepts <= 0;
      source3_accepts <= 0;
    end else begin
      if (in_valid[0] && in_ready[0]) begin
        source0_accepts <= source0_accepts + 1;
      end
      if (in_valid[3] && in_ready[3]) begin
        source3_accepts <= source3_accepts + 1;
      end
    end
  end

  initial begin
    clk = 1'b0;
    rst_n = 1'b0;
    in_valid = '0;
    out_ready = 1'b1;
    for (int source = 0; source < NUM_SOURCES; source++) begin
      in_addr[source] = ADDR_WIDTH'(source);
    end

    repeat (2) @(negedge clk);
    rst_n = 1'b1;
    @(negedge clk);

    // Both sources remain saturated. Fixed priority must repeatedly select
    // source 0, exposing starvation of the lower-priority source 3.
    in_valid[0] = 1'b1;
    in_valid[3] = 1'b1;
    repeat (40) @(negedge clk);
    in_valid = '0;

    if (source0_accepts < 8) begin
      $fatal(1, "expected repeated source 0 service, got %0d", source0_accepts);
    end
    if (source3_accepts != 0) begin
      $fatal(1, "fixed-priority baseline unexpectedly serviced source 3");
    end

    $display("PASS: fixed-priority starvation reproduced (source0=%0d source3=%0d)",
             source0_accepts, source3_accepts);
    $finish;
  end
endmodule
