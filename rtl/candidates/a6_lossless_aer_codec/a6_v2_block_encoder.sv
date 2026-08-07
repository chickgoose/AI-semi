`timescale 1ns/1ps
module a6_v2_block_encoder #(
  parameter int ADDRESS_WIDTH = 4,
  parameter int BLOCK_SIZE = 16,
  parameter int MAX_BITS = ADDRESS_WIDTH * BLOCK_SIZE,
  parameter int IDLE_FLUSH_CYCLES = 16
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
  typedef enum logic [3:0] {
    FILL, PREPARE, DICT_VALUES, DICT_INDICES, DICT_FOOTER, SEND, DELIMIT
  } state_t;
  state_t state;

  logic [MAX_BITS-1:0] raw_bits;
  logic [MAX_BITS-1:0] token_bits;
  logic [MAX_BITS-1:0] send_shift;
  logic [ADDRESS_WIDTH-1:0] dictionary_values [0:BLOCK_SIZE-1];
  logic [3:0] dictionary_indices [0:BLOCK_SIZE-1];
  logic [4:0] block_count;
  logic [4:0] dictionary_count;
  logic [6:0] token_length;
  logic token_previous_valid;
  logic [ADDRESS_WIDTH-1:0] token_previous_address;
  logic [6:0] send_length;
  logic [6:0] dictionary_cursor;
  logic [4:0] dictionary_build_index;
  logic [2:0] dictionary_index_width;
  logic [1:0] dictionary_padding;
  logic [4:0] idle_count;

  logic [6:0] token_code;
  logic [3:0] token_code_length;
  logic dictionary_match;
  logic [3:0] dictionary_match_index;
  integer raw_length_comb;
  integer token_padding_comb;
  integer token_framed_length_comb;
  integer dictionary_width_comb;
  integer dictionary_payload_length_comb;
  integer dictionary_padding_comb;
  integer dictionary_framed_length_comb;
  integer selected_mode_comb;
  integer selected_length_comb;
  integer i;
  integer write_bit;

  initial begin
    if ((ADDRESS_WIDTH != 4) || (BLOCK_SIZE != 16) || (MAX_BITS != 64))
      $error("A6 v2 frozen implementation requires 16 four-bit addresses");
  end

  always_comb begin
    token_code = '0;
    if (token_previous_valid && (event_address == token_previous_address)) begin
      token_code[0] = 1'b0;
      token_code_length = 1;
    end else if (token_previous_valid && (token_previous_address != 4'hf) &&
                 (event_address == token_previous_address + 1'b1)) begin
      token_code[2:0] = 3'b011; // serialized order is 110
      token_code_length = 3;
    end else if (token_previous_valid && (token_previous_address != 4'h0) &&
                 (event_address == token_previous_address - 1'b1)) begin
      token_code[2:0] = 3'b111;
      token_code_length = 3;
    end else begin
      token_code[0] = 1'b1;
      token_code[1] = 1'b0;
      token_code[2] = 1'b1;
      for (i = 0; i < ADDRESS_WIDTH; i = i + 1)
        token_code[3+i] = event_address[ADDRESS_WIDTH-1-i];
      token_code_length = 7;
    end

    dictionary_match = 1'b0;
    dictionary_match_index = '0;
    for (i = 0; i < BLOCK_SIZE; i = i + 1) begin
      if (!dictionary_match && (i < dictionary_count) &&
          (dictionary_values[i] == event_address)) begin
        dictionary_match = 1'b1;
        dictionary_match_index = 4'(i);
      end
    end

    raw_length_comb = 4 * block_count;
    token_padding_comb = (1 - ((token_length + 2) % 4) + 4) % 4;
    token_framed_length_comb = token_length + token_padding_comb + 2;
    if (dictionary_count <= 1)
      dictionary_width_comb = 0;
    else if (dictionary_count <= 2)
      dictionary_width_comb = 1;
    else if (dictionary_count <= 4)
      dictionary_width_comb = 2;
    else if (dictionary_count <= 8)
      dictionary_width_comb = 3;
    else
      dictionary_width_comb = 4;
    dictionary_payload_length_comb =
      5 + 4*dictionary_count + BLOCK_SIZE*dictionary_width_comb;
    dictionary_padding_comb =
      (1 - ((dictionary_payload_length_comb + 2) % 4) + 4) % 4;
    dictionary_framed_length_comb =
      dictionary_payload_length_comb + dictionary_padding_comb + 2;

    selected_mode_comb = 0; // raw
    selected_length_comb = raw_length_comb;
    if (token_framed_length_comb < selected_length_comb) begin
      selected_mode_comb = 1;
      selected_length_comb = token_framed_length_comb;
    end
    if ((block_count == BLOCK_SIZE) &&
        (dictionary_framed_length_comb < selected_length_comb)) begin
      selected_mode_comb = 2;
      selected_length_comb = dictionary_framed_length_comb;
    end

    event_ready = (state == FILL) && (block_count < BLOCK_SIZE);
    link_count = '0;
    link_data = '0;
    if (state == SEND) begin
      link_count = (send_length == 1) ? 2'd1 : 2'd2;
      link_data[1] = send_shift[0];
      if (send_length > 1)
        link_data[0] = send_shift[1];
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state <= FILL;
      raw_bits <= '0;
      token_bits <= {{(MAX_BITS-1){1'b0}}, 1'b0};
      send_shift <= '0;
      block_count <= '0;
      dictionary_count <= '0;
      token_length <= 1;
      token_previous_valid <= 1'b0;
      token_previous_address <= '0;
      send_length <= '0;
      dictionary_cursor <= '0;
      dictionary_build_index <= '0;
      dictionary_index_width <= '0;
      dictionary_padding <= '0;
      idle_count <= '0;
    end else begin
      case (state)
        FILL: begin
          if (event_valid && event_ready) begin
            idle_count <= '0;
            for (write_bit = 0; write_bit < ADDRESS_WIDTH;
                 write_bit = write_bit + 1)
              raw_bits[4*block_count[3:0]+write_bit] <=
                event_address[ADDRESS_WIDTH-1-write_bit];
            if (token_length + token_code_length <= MAX_BITS)
              for (write_bit = 0; write_bit < 7; write_bit = write_bit + 1)
                if (write_bit < token_code_length)
                  token_bits[token_length+write_bit] <= token_code[write_bit];
            token_length <= token_length + token_code_length;
            token_previous_valid <= 1'b1;
            token_previous_address <= event_address;
            if (dictionary_match) begin
              dictionary_indices[block_count[3:0]] <= dictionary_match_index;
            end else begin
              dictionary_values[dictionary_count[3:0]] <= event_address;
              dictionary_indices[block_count[3:0]] <= dictionary_count[3:0];
              dictionary_count <= dictionary_count + 1'b1;
            end
            block_count <= block_count + 1'b1;
            if (block_count == BLOCK_SIZE-1)
              state <= PREPARE;
          end else if (block_count != 0) begin
            if (idle_count + 1'b1 >= IDLE_FLUSH_CYCLES) begin
              idle_count <= '0;
              state <= PREPARE;
            end else begin
              idle_count <= idle_count + 1'b1;
            end
          end else begin
            idle_count <= '0;
          end
        end
        PREPARE: begin
          if (selected_mode_comb == 0) begin
            send_shift <= raw_bits;
            send_length <= 7'(selected_length_comb);
            state <= SEND;
          end else if (selected_mode_comb == 1) begin
            token_bits[token_length+token_padding_comb] <=
              token_padding_comb[1];
            token_bits[token_length+token_padding_comb+1] <=
              token_padding_comb[0];
            send_shift <= token_bits;
            send_shift[token_length+token_padding_comb] <=
              token_padding_comb[1];
            send_shift[token_length+token_padding_comb+1] <=
              token_padding_comb[0];
            send_length <= 7'(selected_length_comb);
            state <= SEND;
          end else begin
            send_shift <= '0;
            send_shift[0] <= 1'b1;
            send_shift[1] <= (dictionary_count-1) >> 3;
            send_shift[2] <= (dictionary_count-1) >> 2;
            send_shift[3] <= (dictionary_count-1) >> 1;
            send_shift[4] <= (dictionary_count-1);
            dictionary_cursor <= 7'd5;
            dictionary_build_index <= '0;
            dictionary_index_width <= 3'(dictionary_width_comb);
            dictionary_padding <= 2'(dictionary_padding_comb);
            state <= DICT_VALUES;
          end
        end
        DICT_VALUES: begin
          for (write_bit = 0; write_bit < ADDRESS_WIDTH;
               write_bit = write_bit + 1)
            send_shift[dictionary_cursor+write_bit] <=
              dictionary_values[dictionary_build_index[3:0]]
                [ADDRESS_WIDTH-1-write_bit];
          dictionary_cursor <= dictionary_cursor + ADDRESS_WIDTH;
          if (dictionary_build_index + 1'b1 >= dictionary_count) begin
            dictionary_build_index <= '0;
            if (dictionary_index_width == 0)
              state <= DICT_FOOTER;
            else
              state <= DICT_INDICES;
          end else begin
            dictionary_build_index <= dictionary_build_index + 1'b1;
          end
        end
        DICT_INDICES: begin
          for (write_bit = 0; write_bit < 4; write_bit = write_bit + 1)
            if (write_bit < dictionary_index_width)
              send_shift[dictionary_cursor+write_bit] <=
                dictionary_indices[dictionary_build_index[3:0]]
                  [dictionary_index_width-1-write_bit];
          dictionary_cursor <= dictionary_cursor + dictionary_index_width;
          if (dictionary_build_index == BLOCK_SIZE-1) begin
            dictionary_build_index <= '0;
            state <= DICT_FOOTER;
          end else begin
            dictionary_build_index <= dictionary_build_index + 1'b1;
          end
        end
        DICT_FOOTER: begin
          send_shift[dictionary_cursor+dictionary_padding] <=
            dictionary_padding[1];
          send_shift[dictionary_cursor+dictionary_padding+1] <=
            dictionary_padding[0];
          send_length <= dictionary_cursor + dictionary_padding + 2;
          state <= SEND;
        end
        SEND: begin
          if (link_ready) begin
            if (send_length <= 2) begin
              send_shift <= '0;
              send_length <= '0;
              state <= DELIMIT;
            end else begin
              send_shift <= send_shift >> 2;
              send_length <= send_length - 2;
            end
          end
        end
        DELIMIT: begin
          raw_bits <= '0;
          token_bits <= '0;
          token_bits[0] <= 1'b0;
          token_length <= 1;
          block_count <= '0;
          dictionary_count <= '0;
          idle_count <= '0;
          state <= FILL;
        end
        default: state <= FILL;
      endcase
    end
  end
endmodule
