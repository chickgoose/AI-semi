`timescale 1ns/1ps

module a7_w4_structural_compare_tb #(
  parameter int STYLE = 0
);
  logic ref_clk_i, sample_clk_i, rst_n, event_valid_i;
  logic [3:0] event_addr_i;
  logic event_ready_o, link_clk_o, retire_toggle_o;
  logic [3:0] link_data_observe_o, retire_addr_o;
  logic [3:0] expected [0:63];
  integer written, read, index;
  logic expected_toggle;

  a7_w4_structural_compare_top #(.STYLE(STYLE)) dut (.*);

  initial begin ref_clk_i = 1'b0; forever #8ns ref_clk_i = ~ref_clk_i; end
  initial begin sample_clk_i = 1'b0; #12ns; sample_clk_i = 1'b1;
    forever #8ns sample_clk_i = ~sample_clk_i; end

  always @(retire_toggle_o) begin
    if (rst_n) begin
      #1ps;
      if (read >= written)
        $fatal(1, "STYLE=%0d unexpected retirement", STYLE);
      if (retire_addr_o !== expected[read])
        $fatal(1, "STYLE=%0d identity mismatch got=%h expected=%h",
               STYLE, retire_addr_o, expected[read]);
      expected_toggle = ~expected_toggle;
      if (retire_toggle_o !== expected_toggle)
        $fatal(1, "STYLE=%0d retirement toggle mismatch", STYLE);
      read = read + 1;
    end
  end

  initial begin
    rst_n = 1'b0; event_valid_i = 1'b0; event_addr_i = '0;
    written = 0; read = 0; expected_toggle = 1'b0;
    repeat (3) @(posedge ref_clk_i);
    rst_n = 1'b1;
    for (index = 0; index < 32; index = index + 1) begin
      @(negedge ref_clk_i);
      while (!event_ready_o) @(negedge ref_clk_i);
      event_valid_i = 1'b1;
      event_addr_i = 4'(((index * 5) + 3) & 15);
      expected[written] = event_addr_i;
      written = written + 1;
      @(negedge ref_clk_i);
      event_valid_i = 1'b0;
    end
    while (read != written) @(posedge ref_clk_i);
    repeat (2) @(posedge ref_clk_i);
    if (read != 32)
      $fatal(1, "STYLE=%0d incomplete drain read=%0d", STYLE, read);
    $display("A7_W4_EQUAL_TOP_IDENTITY_PASS style=%0d events=32", STYLE);
    $finish;
  end
endmodule
