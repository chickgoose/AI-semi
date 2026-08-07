`timescale 1ns/1ps
module a6_v2_block_decoder #(
  parameter int ADDRESS_WIDTH = 4,
  parameter int BLOCK_SIZE = 16,
  parameter int MAX_BITS = ADDRESS_WIDTH * BLOCK_SIZE
) (
  input  logic                     clk,
  input  logic                     rst_n,
  input  logic [1:0]               link_count,
  input  logic [1:0]               link_data,
  output logic                     link_ready,
  output logic                     event_valid,
  output logic [ADDRESS_WIDTH-1:0] event_address,
  input  logic                     event_ready,
  output logic                     decode_error
);
  typedef enum logic [3:0] {
    RECEIVE, CLASSIFY, RAW_VALUES, TOKEN_VALUES,
    DICT_VALUES, DICT_OUTPUT, RETIRE
  } state_t;
  state_t state;

  logic [MAX_BITS-1:0] bit_buffer;
  logic [6:0] bit_count;
  logic in_block;
  logic previous_valid;
  logic [ADDRESS_WIDTH-1:0] previous_address;
  logic temporary_previous_valid;
  logic [ADDRESS_WIDTH-1:0] temporary_previous_address;
  logic [ADDRESS_WIDTH-1:0] output_memory [0:BLOCK_SIZE-1];
  logic [ADDRESS_WIDTH-1:0] dictionary_values [0:BLOCK_SIZE-1];
  logic [4:0] output_count;
  logic [4:0] output_index;
  logic [6:0] payload_length;
  logic [6:0] cursor;
  logic [4:0] dictionary_count;
  logic [2:0] dictionary_index_width;
  logic [4:0] dictionary_build_index;

  logic [ADDRESS_WIDTH-1:0] raw_address_comb;
  logic [ADDRESS_WIDTH-1:0] literal_address_comb;
  logic [ADDRESS_WIDTH-1:0] dictionary_literal_comb;
  logic [3:0] dictionary_index_comb;
  logic [ADDRESS_WIDTH-1:0] dictionary_value_comb;
  integer padding_comb;
  integer payload_length_comb;
  integer dictionary_count_comb;
  integer dictionary_width_comb;
  integer dictionary_expected_length_comb;
  logic padding_error_comb;
  integer i;

  always_comb begin
    padding_comb = 0;
    payload_length_comb = 0;
    padding_error_comb = 1'b0;
    if (bit_count >= 3) begin
      padding_comb = {bit_buffer[bit_count-2], bit_buffer[bit_count-1]};
      payload_length_comb = bit_count - padding_comb - 2;
      for (i = 0; i < 3; i = i + 1)
        if ((i < padding_comb) && bit_buffer[payload_length_comb+i])
          padding_error_comb = 1'b1;
    end

    dictionary_count_comb =
      {bit_buffer[1], bit_buffer[2], bit_buffer[3], bit_buffer[4]} + 1;
    if (dictionary_count_comb <= 1)
      dictionary_width_comb = 0;
    else if (dictionary_count_comb <= 2)
      dictionary_width_comb = 1;
    else if (dictionary_count_comb <= 4)
      dictionary_width_comb = 2;
    else if (dictionary_count_comb <= 8)
      dictionary_width_comb = 3;
    else
      dictionary_width_comb = 4;
    dictionary_expected_length_comb =
      5 + 4*dictionary_count_comb + BLOCK_SIZE*dictionary_width_comb;

    raw_address_comb = '0;
    literal_address_comb = '0;
    dictionary_literal_comb = '0;
    for (i = 0; i < ADDRESS_WIDTH; i = i + 1) begin
      raw_address_comb[ADDRESS_WIDTH-1-i] =
        bit_buffer[4*dictionary_build_index[3:0]+i];
      literal_address_comb[ADDRESS_WIDTH-1-i] = bit_buffer[cursor+3+i];
      dictionary_literal_comb[ADDRESS_WIDTH-1-i] = bit_buffer[cursor+i];
    end

    dictionary_index_comb = '0;
    for (i = 0; i < 4; i = i + 1)
      if (i < dictionary_index_width)
        dictionary_index_comb = (dictionary_index_comb << 1) |
          bit_buffer[cursor+i];
    dictionary_value_comb = dictionary_values[dictionary_index_comb];

    link_ready = (state == RECEIVE) && (bit_count <= MAX_BITS-2);
    event_valid = (state == RETIRE) && (output_index < output_count);
    event_address = output_memory[output_index[3:0]];
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state <= RECEIVE;
      bit_buffer <= '0;
      bit_count <= '0;
      in_block <= 1'b0;
      previous_valid <= 1'b0;
      previous_address <= '0;
      temporary_previous_valid <= 1'b0;
      temporary_previous_address <= '0;
      output_count <= '0;
      output_index <= '0;
      payload_length <= '0;
      cursor <= '0;
      dictionary_count <= '0;
      dictionary_index_width <= '0;
      dictionary_build_index <= '0;
      decode_error <= 1'b0;
    end else begin
      case (state)
        RECEIVE: begin
          if ((link_count != 0) && link_ready) begin
            bit_buffer[bit_count[5:0]] <= link_data[1];
            if (link_count == 2)
              bit_buffer[bit_count[5:0]+1'b1] <= link_data[0];
            bit_count <= bit_count + link_count;
            in_block <= 1'b1;
          end else if ((link_count == 0) && in_block) begin
            state <= CLASSIFY;
            in_block <= 1'b0;
          end
        end
        CLASSIFY: begin
          output_count <= '0;
          output_index <= '0;
          dictionary_build_index <= '0;
          if ((bit_count != 0) && ((bit_count % 4) == 0) &&
              ((bit_count >> 2) <= BLOCK_SIZE)) begin
            output_count <= 5'(bit_count >> 2);
            state <= RAW_VALUES;
          end else if ((bit_count >= 3) && ((bit_count % 4) == 1) &&
                       (payload_length_comb > 0) && !padding_error_comb) begin
            payload_length <= 7'(payload_length_comb);
            if (!bit_buffer[0]) begin
              cursor <= 1;
              temporary_previous_valid <= previous_valid;
              temporary_previous_address <= previous_address;
              state <= TOKEN_VALUES;
            end else if ((payload_length_comb >= 5) &&
                         (dictionary_count_comb >= 1) &&
                         (dictionary_count_comb <= BLOCK_SIZE) &&
                         (payload_length_comb == dictionary_expected_length_comb)) begin
              dictionary_count <= 5'(dictionary_count_comb);
              dictionary_index_width <= 3'(dictionary_width_comb);
              cursor <= 5;
              state <= DICT_VALUES;
            end else begin
              decode_error <= 1'b1;
              bit_buffer <= '0;
              bit_count <= '0;
              state <= RECEIVE;
            end
          end else begin
            decode_error <= 1'b1;
            bit_buffer <= '0;
            bit_count <= '0;
            state <= RECEIVE;
          end
        end
        RAW_VALUES: begin
          output_memory[dictionary_build_index[3:0]] <= raw_address_comb;
          if (dictionary_build_index + 1'b1 >= output_count) begin
            previous_valid <= 1'b1;
            previous_address <= raw_address_comb;
            dictionary_build_index <= '0;
            state <= RETIRE;
          end else begin
            dictionary_build_index <= dictionary_build_index + 1'b1;
          end
        end
        TOKEN_VALUES: begin
          if (cursor == payload_length) begin
            if (output_count == 0) begin
              decode_error <= 1'b1;
              bit_buffer <= '0;
              bit_count <= '0;
              state <= RECEIVE;
            end else begin
              previous_valid <= temporary_previous_valid;
              previous_address <= temporary_previous_address;
              state <= RETIRE;
            end
          end else if ((cursor > payload_length) || (output_count >= BLOCK_SIZE)) begin
            decode_error <= 1'b1;
            bit_buffer <= '0;
            bit_count <= '0;
            state <= RECEIVE;
          end else if (!bit_buffer[cursor]) begin
            if (!temporary_previous_valid) begin
              decode_error <= 1'b1;
              bit_buffer <= '0;
              bit_count <= '0;
              state <= RECEIVE;
            end else begin
              output_memory[output_count[3:0]] <= temporary_previous_address;
              output_count <= output_count + 1'b1;
              cursor <= cursor + 1'b1;
            end
          end else if (cursor + 3 > payload_length) begin
            decode_error <= 1'b1;
            bit_buffer <= '0;
            bit_count <= '0;
            state <= RECEIVE;
          end else begin
            case ({bit_buffer[cursor+1], bit_buffer[cursor+2]})
              2'b01: begin
                if (cursor + 7 > payload_length) begin
                  decode_error <= 1'b1;
                  bit_buffer <= '0;
                  bit_count <= '0;
                  state <= RECEIVE;
                end else begin
                  output_memory[output_count[3:0]] <= literal_address_comb;
                  output_count <= output_count + 1'b1;
                  temporary_previous_valid <= 1'b1;
                  temporary_previous_address <= literal_address_comb;
                  cursor <= cursor + 7;
                end
              end
              2'b10: begin
                if (!temporary_previous_valid ||
                    (temporary_previous_address == 4'hf)) begin
                  decode_error <= 1'b1;
                  bit_buffer <= '0;
                  bit_count <= '0;
                  state <= RECEIVE;
                end else begin
                  output_memory[output_count[3:0]] <=
                    temporary_previous_address + 1'b1;
                  output_count <= output_count + 1'b1;
                  temporary_previous_address <=
                    temporary_previous_address + 1'b1;
                  cursor <= cursor + 3;
                end
              end
              2'b11: begin
                if (!temporary_previous_valid ||
                    (temporary_previous_address == 4'h0)) begin
                  decode_error <= 1'b1;
                  bit_buffer <= '0;
                  bit_count <= '0;
                  state <= RECEIVE;
                end else begin
                  output_memory[output_count[3:0]] <=
                    temporary_previous_address - 1'b1;
                  output_count <= output_count + 1'b1;
                  temporary_previous_address <=
                    temporary_previous_address - 1'b1;
                  cursor <= cursor + 3;
                end
              end
              default: begin
                decode_error <= 1'b1;
                bit_buffer <= '0;
                bit_count <= '0;
                state <= RECEIVE;
              end
            endcase
          end
        end
        DICT_VALUES: begin
          dictionary_values[dictionary_build_index[3:0]] <=
            dictionary_literal_comb;
          cursor <= cursor + ADDRESS_WIDTH;
          if (dictionary_build_index + 1'b1 >= dictionary_count) begin
            dictionary_build_index <= '0;
            state <= DICT_OUTPUT;
          end else begin
            dictionary_build_index <= dictionary_build_index + 1'b1;
          end
        end
        DICT_OUTPUT: begin
          if (dictionary_index_comb >= dictionary_count) begin
            decode_error <= 1'b1;
            bit_buffer <= '0;
            bit_count <= '0;
            state <= RECEIVE;
          end else begin
            output_memory[dictionary_build_index[3:0]] <= dictionary_value_comb;
            cursor <= cursor + dictionary_index_width;
            if (dictionary_build_index == BLOCK_SIZE-1) begin
              output_count <= BLOCK_SIZE;
              previous_valid <= 1'b1;
              previous_address <= dictionary_value_comb;
              dictionary_build_index <= '0;
              state <= RETIRE;
            end else begin
              dictionary_build_index <= dictionary_build_index + 1'b1;
            end
          end
        end
        RETIRE: begin
          if (event_valid && event_ready) begin
            if (output_index + 1'b1 >= output_count) begin
              output_index <= '0;
              output_count <= '0;
              bit_buffer <= '0;
              bit_count <= '0;
              state <= RECEIVE;
            end else begin
              output_index <= output_index + 1'b1;
            end
          end
        end
        default: state <= RECEIVE;
      endcase
    end
  end
endmodule
