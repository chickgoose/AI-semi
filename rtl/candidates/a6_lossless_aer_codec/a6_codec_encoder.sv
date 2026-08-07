`timescale 1ns/1ps
module a6_codec_encoder #(
  parameter int ADDRESS_WIDTH = 4,
  parameter int RUN_MAX = 9,
  parameter int TOKEN_WIDTH = ADDRESS_WIDTH + 9
) (
  input  logic                     clk,
  input  logic                     rst_n,
  input  logic                     event_valid,
  output logic                     event_ready,
  input  logic [ADDRESS_WIDTH-1:0] event_address,
  output logic [1:0]               link_count,
  output logic [1:0]               link_data,
  input  logic                     link_ready
);
  localparam int RUN_COUNT_WIDTH = $clog2(RUN_MAX + 1);
  localparam int LENGTH_WIDTH = $clog2(TOKEN_WIDTH + 1);

  logic run_valid;
  logic [ADDRESS_WIDTH-1:0] run_address;
  logic [RUN_COUNT_WIDTH-1:0] run_count;
  logic previous_valid;
  logic [ADDRESS_WIDTH-1:0] previous_address;
  logic [TOKEN_WIDTH-1:0] token_shift;
  logic [LENGTH_WIDTH-1:0] token_length;
  logic [TOKEN_WIDTH+LENGTH_WIDTH-1:0] built_token;
  logic [LENGTH_WIDTH-1:0] built_length;
  logic [TOKEN_WIDTH-1:0] built_bits;
  logic serializer_empty;
  logic same_run;
  logic flush_run;

  function automatic logic [TOKEN_WIDTH+LENGTH_WIDTH-1:0] build_token(
    input logic [ADDRESS_WIDTH-1:0] address,
    input logic [RUN_COUNT_WIDTH-1:0] count,
    input logic history_valid,
    input logic [ADDRESS_WIDTH-1:0] history_address
  );
    logic [TOKEN_WIDTH-1:0] bits;
    logic [LENGTH_WIDTH-1:0] length;
    logic [2:0] count_code;
    integer i;
    begin
      bits = '0;
      length = '0;
      if (history_valid && (address == history_address)) begin
        if (count == 1) begin
          bits[0] = 1'b0;
          length = LENGTH_WIDTH'(1);
        end else begin
          count_code = 3'(count - 2);
          bits[0] = 1'b1;
          bits[1] = 1'b0;
          bits[2] = 1'b0;
          bits[3] = count_code[2];
          bits[4] = count_code[1];
          bits[5] = count_code[0];
          length = LENGTH_WIDTH'(6);
        end
      end else begin
        if (history_valid && (history_address != {ADDRESS_WIDTH{1'b1}}) &&
            (address == history_address + 1'b1)) begin
          bits[0] = 1'b1;
          bits[1] = 1'b1;
          bits[2] = 1'b0;
          length = LENGTH_WIDTH'(3);
        end else if (history_valid && (history_address != '0) &&
                     (address == history_address - 1'b1)) begin
          bits[0] = 1'b1;
          bits[1] = 1'b1;
          bits[2] = 1'b1;
          length = LENGTH_WIDTH'(3);
        end else begin
          bits[0] = 1'b1;
          bits[1] = 1'b0;
          bits[2] = 1'b1;
          for (i = 0; i < ADDRESS_WIDTH; i = i + 1)
            bits[3+i] = address[ADDRESS_WIDTH-1-i];
          length = LENGTH_WIDTH'(3 + ADDRESS_WIDTH);
        end

        if (count == 2) begin
          bits[length] = 1'b0;
          length = length + 1'b1;
        end else if (count > 2) begin
          count_code = 3'(count - 3);
          bits[length] = 1'b1;
          bits[length+1] = 1'b0;
          bits[length+2] = 1'b0;
          bits[length+3] = count_code[2];
          bits[length+4] = count_code[1];
          bits[length+5] = count_code[0];
          length = length + 6;
        end
      end
      build_token = {length, bits};
    end
  endfunction

  always_comb begin
    serializer_empty = (token_length == 0);
    same_run = run_valid && event_valid && (event_address == run_address);
    event_ready = !run_valid ||
                  (same_run && (run_count < RUN_COUNT_WIDTH'(RUN_MAX))) ||
                  serializer_empty;
    flush_run = run_valid && serializer_empty &&
                (!event_valid || !event_ready);

    built_token = build_token(run_address, run_count,
                              previous_valid, previous_address);
    built_bits = built_token[TOKEN_WIDTH-1:0];
    built_length = built_token[TOKEN_WIDTH+LENGTH_WIDTH-1:TOKEN_WIDTH];

    link_count = '0;
    link_data = '0;
    if (token_length != 0) begin
      if (token_length == 1)
        link_count = 2'd1;
      else
        link_count = 2'd2;
      link_data[1] = token_shift[0];
      if (token_length > 1)
        link_data[0] = token_shift[1];
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      run_valid <= 1'b0;
      run_address <= '0;
      run_count <= '0;
      previous_valid <= 1'b0;
      previous_address <= '0;
      token_shift <= '0;
      token_length <= '0;
    end else begin
      if ((token_length != 0) && link_ready) begin
        if (token_length == 1) begin
          token_shift <= '0;
          token_length <= '0;
        end else begin
          token_shift <= token_shift >> 2;
          token_length <= token_length - 2;
        end
      end

      if (event_valid && event_ready) begin
        if (!run_valid) begin
          run_valid <= 1'b1;
          run_address <= event_address;
          run_count <= RUN_COUNT_WIDTH'(1);
        end else if ((event_address == run_address) &&
                     (run_count < RUN_COUNT_WIDTH'(RUN_MAX))) begin
          run_count <= run_count + 1'b1;
        end else begin
          token_shift <= built_bits;
          token_length <= built_length;
          previous_valid <= 1'b1;
          previous_address <= run_address;
          run_valid <= 1'b1;
          run_address <= event_address;
          run_count <= RUN_COUNT_WIDTH'(1);
        end
      end else if (flush_run) begin
        token_shift <= built_bits;
        token_length <= built_length;
        previous_valid <= 1'b1;
        previous_address <= run_address;
        run_valid <= 1'b0;
        run_count <= '0;
      end
    end
  end
endmodule
