`timescale 1ns/1ps

module a3_refractory_wta_tb;
  localparam int N = 4;
  localparam int ADDR_WIDTH = 16;
  localparam int SOURCE_WIDTH = 2;
  logic clk = 1'b0;
  logic rst_n;
  logic [N-1:0] source_valid;
  logic [N-1:0] source_ready;
  logic [ADDR_WIDTH-1:0] source_event [N];
  logic retire_valid;
  logic retire_ready;
  logic [ADDR_WIDTH-1:0] retire_event;
  logic [SOURCE_WIDTH-1:0] retire_source;
  logic [ADDR_WIDTH-1:0] held_event;
  integer i;
  integer expected;

  always #5 clk = ~clk;

  a3_refractory_wta #(.NUM_SOURCES(N), .ADDR_WIDTH(ADDR_WIDTH)) dut (.*);

  task automatic reset_dut;
    begin
      rst_n = 1'b0;
      source_valid = '0;
      retire_ready = 1'b1;
      repeat (3) @(posedge clk);
      @(negedge clk);
      rst_n = 1'b1;
    end
  endtask

  initial begin
    for (i = 0; i < N; i = i + 1)
      source_event[i] = ADDR_WIDTH'(16'h100 + i);
    reset_dut();

    // One source remains work conserving despite its refractory marker.
    @(negedge clk);
    source_valid = 4'b1000;
    repeat (4) begin
      #1;
      if (source_ready != 4'b1000)
        $fatal(1, "isolated source was blocked ready=%b", source_ready);
      @(posedge clk);
      @(negedge clk);
    end

    // Permanent fan-in exposes both RR non-equivalence and starvation: the
    // minimal absolute-refractory law alternates only fixed winners 0 and 1.
    source_valid = '1;
    for (i = 0; i < 12; i = i + 1) begin
      #1;
      expected = i[0] ? 1 : 0;
      if (!source_ready[expected] || $countones(source_ready) != 1)
        $fatal(1, "unexpected refractory WTA sequence i=%0d ready=%b", i, source_ready);
      if (source_ready[2] || source_ready[3])
        $fatal(1, "predicted counterexample disappeared ready=%b", source_ready);
      @(posedge clk);
      @(negedge clk);
    end

    // Registered output and policy state hold under downstream stall.
    source_valid = 4'b0100;
    @(posedge clk);
    @(negedge clk);
    retire_ready = 1'b0;
    held_event = retire_event;
    repeat (4) begin
      @(posedge clk);
      #1;
      if (!retire_valid || retire_event !== held_event || (|source_ready))
        $fatal(1, "stall hold failed");
    end
    retire_ready = 1'b1;
    repeat (3) @(posedge clk);
    $display("A3_REFRACTORY_WTA_RTL_PASS rr_divergent=1 persistent_starvation=1");
    $finish;
  end
endmodule
