`timescale 1ns/1ps

module a7_weighted_fovea_ddr_fault_tb;
  localparam time HALF = 8ns;
  logic ref_clk_i, sample_clk_i, rst_n;
  logic [15:0] source_valid;
  logic [15:0] source_ready;
  logic burst_clk_o;
  logic [1:0] burst_data_o;
  logic [3:0] retire_addr_o;
  logic retire_valid_o, drain_idle_o, protocol_fault_o;
  bit fault_seen;
  integer timeout;

  a7_weighted_fovea_ddr dut (.*);

  initial begin ref_clk_i = 1'b0; forever #(HALF) ref_clk_i = ~ref_clk_i; end
  initial begin
    sample_clk_i = 1'b0;
    #12ns sample_clk_i = 1'b1;
    forever #(HALF) sample_clk_i = ~sample_clk_i;
  end

  always @(posedge ref_clk_i) begin
    if (rst_n) begin
      if (source_ready != '0)
        $fatal(1, "negative fixture was incorrectly acknowledged ready=%h",
               source_ready);
      if (protocol_fault_o) begin
        fault_seen = 1'b1;
        if (drain_idle_o)
          $fatal(1, "negative raw fault incorrectly reported drain idle");
      end
      #1ps;
      if (retire_valid_o) begin
        if (!fault_seen || retire_addr_o !== 4'ha)
          $fatal(1, "negative phantom visibility mismatch fault=%b addr=%h",
                 fault_seen, retire_addr_o);
        $fatal(1, "A7_W6_STALE_NO_LIVE_NEGATIVE_CAUGHT addr=a");
      end
    end
  end

  initial begin
    rst_n = 1'b0;
    source_valid = '0;
    fault_seen = 1'b0;
    timeout = 0;
    repeat (3) @(negedge sample_clk_i);
    rst_n = 1'b1;
    while (timeout < 20) begin
      @(posedge ref_clk_i);
      timeout = timeout + 1;
    end
    $fatal(1, "negative stale/no-live injection did not reach final retire");
  end
endmodule
