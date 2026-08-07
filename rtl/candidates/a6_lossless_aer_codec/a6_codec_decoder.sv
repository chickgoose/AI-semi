`timescale 1ns/1ps
module a6_codec_decoder #(
  parameter int ADDRESS_WIDTH = 4,
  parameter int BUFFER_WIDTH = 16
) (
  input  logic                     clk,
  input  logic                     rst_n,
  input  logic [1:0]               link_count,
  input  logic [1:0]               link_data,
  output logic                     link_ready,
  output logic                     event_valid,
  output logic [ADDRESS_WIDTH-1:0] event_address,
  input  logic                     event_ready
);
  localparam int BUFFER_COUNT_WIDTH = $clog2(BUFFER_WIDTH + 1);

  logic [BUFFER_WIDTH-1:0] bit_buffer;
  logic [BUFFER_COUNT_WIDTH-1:0] bit_count;
  logic previous_valid;
  logic [ADDRESS_WIDTH-1:0] previous_address;
  logic [3:0] repeat_remaining;
  logic token_ready;
  logic [3:0] token_length;
  logic [3:0] decoded_count;
  logic [ADDRESS_WIDTH-1:0] decoded_address;
  logic decoded_updates_history;
  integer address_bit;

  always_comb begin
    address_bit = 0;
    token_ready = 1'b0;
    token_length = '0;
    decoded_count = 4'd1;
    decoded_address = previous_address;
    decoded_updates_history = 1'b0;

    if (!event_valid && (bit_count >= 1)) begin
      if (!bit_buffer[0]) begin
        if (previous_valid) begin
          token_ready = 1'b1;
          token_length = 4'd1;
        end
      end else if (bit_count >= 3) begin
        case ({bit_buffer[1], bit_buffer[2]})
          2'b00: begin
            if (previous_valid && (bit_count >= 6)) begin
              token_ready = 1'b1;
              token_length = 4'd6;
              decoded_count = {1'b0, bit_buffer[3], bit_buffer[4],
                               bit_buffer[5]} + 4'd2;
            end
          end
          2'b01: begin
            if (bit_count >= BUFFER_COUNT_WIDTH'(3 + ADDRESS_WIDTH)) begin
              token_ready = 1'b1;
              token_length = 4'(3 + ADDRESS_WIDTH);
              decoded_updates_history = 1'b1;
              for (address_bit = 0; address_bit < ADDRESS_WIDTH;
                   address_bit = address_bit + 1)
                decoded_address[ADDRESS_WIDTH-1-address_bit] =
                  bit_buffer[3+address_bit];
            end
          end
          2'b10: begin
            if (previous_valid && (previous_address != {ADDRESS_WIDTH{1'b1}})) begin
              token_ready = 1'b1;
              token_length = 4'd3;
              decoded_updates_history = 1'b1;
              decoded_address = previous_address + 1'b1;
            end
          end
          2'b11: begin
            if (previous_valid && (previous_address != '0)) begin
              token_ready = 1'b1;
              token_length = 4'd3;
              decoded_updates_history = 1'b1;
              decoded_address = previous_address - 1'b1;
            end
          end
        endcase
      end
    end

    link_ready = !event_valid && !token_ready &&
                 (bit_count <= BUFFER_COUNT_WIDTH'(BUFFER_WIDTH - 2));
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      bit_buffer <= '0;
      bit_count <= '0;
      previous_valid <= 1'b0;
      previous_address <= '0;
      repeat_remaining <= '0;
      event_valid <= 1'b0;
      event_address <= '0;
    end else begin
      if (event_valid && event_ready) begin
        if (repeat_remaining != 0) begin
          repeat_remaining <= repeat_remaining - 1'b1;
          event_valid <= 1'b1;
        end else begin
          event_valid <= 1'b0;
        end
      end

      if (token_ready) begin
        bit_buffer <= bit_buffer >> token_length;
        bit_count <= bit_count - token_length;
        event_valid <= 1'b1;
        event_address <= decoded_address;
        repeat_remaining <= decoded_count - 1'b1;
        if (decoded_updates_history) begin
          previous_valid <= 1'b1;
          previous_address <= decoded_address;
        end
      end else if ((link_count != 0) && link_ready) begin
        bit_buffer[int'(bit_count)] <= link_data[1];
        if (link_count == 2)
          bit_buffer[int'(bit_count)+1] <= link_data[0];
        bit_count <= bit_count + BUFFER_COUNT_WIDTH'(link_count);
      end
    end
  end
endmodule
