`timescale 1ns/1ps
module a6_v2_codec_rtl_tb;
  localparam int EVENT_COUNT = 320;
  logic clk = 1'b0;
  logic rst_n = 1'b0;
  logic input_valid;
  logic input_ready;
  logic [3:0] input_address;
  logic [1:0] link_count;
  logic [1:0] link_data;
  logic link_ready;
  logic output_valid;
  logic [3:0] output_address;
  logic output_ready;
  logic decode_error;
  logic [3:0] expected [0:EVENT_COUNT-1];
  integer sent;
  integer retired;
  integer cycles;
  integer seed;

  always #5 clk = ~clk;

  a6_v2_block_encoder encoder (
    .clk(clk), .rst_n(rst_n), .event_valid(input_valid),
    .event_ready(input_ready), .event_address(input_address),
    .link_count(link_count), .link_data(link_data), .link_ready(link_ready)
  );

  a6_v2_block_decoder decoder (
    .clk(clk), .rst_n(rst_n), .link_count(link_count), .link_data(link_data),
    .link_ready(link_ready), .event_valid(output_valid),
    .event_address(output_address), .event_ready(output_ready),
    .decode_error(decode_error)
  );

  function automatic [3:0] stimulus(input integer index);
    begin
      if (index < 64)
        stimulus = 4'h5;
      else if (index < 128)
        stimulus = (index - 64) & 4'hf;
      else if (index < 192)
        stimulus = ((index / 5) + (index % 3)) & 4'hf;
      else
        stimulus = $urandom(seed) & 4'hf;
    end
  endfunction

  initial begin
    input_valid = 1'b0;
    input_address = '0;
    output_ready = 1'b0;
    sent = 0;
    retired = 0;
    cycles = 0;
    seed = 32'h6a62_2026;
    repeat (5) @(posedge clk);
    rst_n = 1'b1;
    while ((retired < EVENT_COUNT) && (cycles < 200000)) begin
      @(negedge clk);
      cycles = cycles + 1;
      output_ready = (($urandom(seed) & 3) != 0);
      if (sent < EVENT_COUNT) begin
        input_valid = (($urandom(seed) & 7) != 0);
        input_address = stimulus(sent);
      end else begin
        input_valid = 1'b0;
      end
      @(posedge clk);
      if (input_valid && input_ready) begin
        expected[sent] = input_address;
        sent = sent + 1;
      end
      if (output_valid && output_ready) begin
        if (output_address !== expected[retired]) begin
          $display("FAIL mismatch index=%0d expected=%0h actual=%0h",
                   retired, expected[retired], output_address);
          $fatal(1);
        end
        retired = retired + 1;
      end
      if (decode_error) begin
        $display("FAIL decoder asserted decode_error");
        $fatal(1);
      end
    end
    if ((sent != EVENT_COUNT) || (retired != EVENT_COUNT)) begin
      $display("FAIL timeout sent=%0d retired=%0d cycles=%0d",
               sent, retired, cycles);
      $fatal(1);
    end
    $display("PASS A6 v2 RTL roundtrip events=%0d cycles=%0d", retired, cycles);
    $finish;
  end
endmodule
