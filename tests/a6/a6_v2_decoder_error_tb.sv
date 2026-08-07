`timescale 1ns/1ps
module a6_v2_decoder_error_tb;
  logic clk = 1'b0;
  logic rst_n = 1'b0;
  logic [1:0] link_count;
  logic [1:0] link_data;
  logic link_ready;
  logic event_valid;
  logic [3:0] event_address;
  logic decode_error;

  always #5 clk = ~clk;

  a6_v2_block_decoder dut (
    .clk(clk), .rst_n(rst_n), .link_count(link_count), .link_data(link_data),
    .link_ready(link_ready), .event_valid(event_valid),
    .event_address(event_address), .event_ready(1'b1),
    .decode_error(decode_error)
  );

  task automatic send_pair(input logic [1:0] count, input logic [1:0] data);
    begin
      @(negedge clk);
      while (!link_ready) @(negedge clk);
      link_count = count;
      link_data = data;
      @(posedge clk);
    end
  endtask

  initial begin
    link_count = 0;
    link_data = 0;
    repeat (4) @(posedge clk);
    rst_n = 1'b1;
    // Five serialized zero bits form a compressed token block containing
    // SAME tokens before any history.  A conforming decoder must reject it.
    send_pair(2, 2'b00);
    send_pair(2, 2'b00);
    send_pair(1, 2'b00);
    @(negedge clk);
    link_count = 0;
    repeat (5) @(posedge clk);
    if (!decode_error || event_valid) begin
      $display("FAIL malformed block was not rejected");
      $fatal(1);
    end
    $display("PASS A6 v2 decoder malformed-stream gate");
    $finish;
  end
endmodule
