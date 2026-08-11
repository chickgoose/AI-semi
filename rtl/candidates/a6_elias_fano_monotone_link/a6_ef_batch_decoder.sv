`timescale 1ns/1ps

module a6_ef_batch_decoder #(
  parameter int NUM_SOURCES = 16,
  parameter int MAX_BATCH = 16,
  parameter int ADDRESS_WIDTH = $clog2(NUM_SOURCES),
  parameter int COUNT_WIDTH = $clog2(MAX_BATCH + 1),
  parameter int FIFO_DEPTH = 2 * MAX_BATCH,
  parameter int FIFO_PTR_WIDTH = $clog2(FIFO_DEPTH),
  parameter int FIFO_COUNT_WIDTH = $clog2(FIFO_DEPTH + 1)
) (
  input  logic                       clk,
  input  logic                       rst_n,
  input  logic [1:0]                 link_count,
  input  logic [1:0]                 link_data,
  output logic                       link_ready,
  output logic                       event_valid,
  input  logic                       event_ready,
  output logic [ADDRESS_WIDTH-1:0]   event_address,
  output logic                       decode_error
);
  typedef enum logic [1:0] {RAW, HEADER, HIGH, LOW} parse_state_t;
  parse_state_t state, state_next;

  logic [ADDRESS_WIDTH-1:0] raw_accum, raw_accum_next;
  integer raw_count, raw_count_next;
  logic [COUNT_WIDTH-1:0] header_accum, header_accum_next;
  integer header_count, header_count_next;
  integer frame_count, frame_count_next;
  integer low_width_reg, low_width_next;
  integer high_cursor, high_cursor_next;
  integer high_index, high_index_next;
  logic [ADDRESS_WIDTH-1:0] high_values [0:MAX_BATCH-1];
  logic [ADDRESS_WIDTH-1:0] high_values_next [0:MAX_BATCH-1];
  logic [ADDRESS_WIDTH-1:0] decoded_values [0:MAX_BATCH-1];
  logic [ADDRESS_WIDTH-1:0] decoded_values_next [0:MAX_BATCH-1];
  logic [ADDRESS_WIDTH-1:0] low_accum, low_accum_next;
  integer low_count, low_count_next;
  integer low_index, low_index_next;

  logic [ADDRESS_WIDTH-1:0] fifo_memory [0:FIFO_DEPTH-1];
  logic [FIFO_PTR_WIDTH-1:0] write_pointer;
  logic [FIFO_PTR_WIDTH-1:0] read_pointer;
  logic [FIFO_COUNT_WIDTH-1:0] fifo_count;

  logic [ADDRESS_WIDTH-1:0] push_values [0:MAX_BATCH-1];
  integer push_count;
  logic parser_error;
  logic pop_event;
  integer free_slots;
  integer beat_index;
  integer value_comb;
  integer parsed_count;
  integer parsed_low_width;
  integer width_index;
  integer comb_i;
  integer seq_i;
  logic serial_bit;

  initial begin
    if ((NUM_SOURCES < 2) || ((NUM_SOURCES & (NUM_SOURCES-1)) != 0))
      $error("A6 EF decoder requires a power-of-two source universe");
    if ((MAX_BATCH < 1) || (MAX_BATCH > NUM_SOURCES))
      $error("A6 EF decoder has an invalid MAX_BATCH");
    if ((ADDRESS_WIDTH % 2) != 0)
      $error("A6 EF raw escape requires an even address width");
    if ((FIFO_DEPTH < 2*MAX_BATCH) || ((FIFO_DEPTH & (FIFO_DEPTH-1)) != 0))
      $error("A6 EF decoder FIFO must be power-of-two and at least two batches");
  end

  always_comb begin
    event_valid = (fifo_count != 0);
    event_address = fifo_memory[read_pointer];
    pop_event = event_valid && event_ready;
    free_slots = FIFO_DEPTH - fifo_count + (pop_event ? 1 : 0);
    link_ready = !decode_error;
    if (state == RAW) begin
      if (link_count == 1)
        link_ready = !decode_error && (raw_count == 0) &&
                     (free_slots >= MAX_BATCH);
      else if (link_count == 2)
        link_ready = !decode_error && (free_slots >= 1);
    end
  end

  always_comb begin
    state_next = state;
    raw_accum_next = raw_accum;
    raw_count_next = raw_count;
    header_accum_next = header_accum;
    header_count_next = header_count;
    frame_count_next = frame_count;
    low_width_next = low_width_reg;
    high_cursor_next = high_cursor;
    high_index_next = high_index;
    low_accum_next = low_accum;
    low_count_next = low_count;
    low_index_next = low_index;
    parser_error = 1'b0;
    push_count = 0;
    parsed_count = 0;
    parsed_low_width = 0;
    width_index = 0;
    comb_i = 0;
    beat_index = 0;
    value_comb = 0;
    serial_bit = 1'b0;
    for (comb_i = 0; comb_i < MAX_BATCH; comb_i = comb_i + 1) begin
      high_values_next[comb_i] = high_values[comb_i];
      decoded_values_next[comb_i] = decoded_values[comb_i];
      push_values[comb_i] = '0;
    end

    if ((link_count != 0) && link_ready) begin
      if (link_count > 2) begin
        parser_error = 1'b1;
      end else if (state == RAW && link_count == 1) begin
        if ((raw_count != 0) || (link_data[1] != 1'b1)) begin
          parser_error = 1'b1;
        end else begin
          state_next = HEADER;
          header_accum_next = '0;
          header_count_next = 0;
        end
      end else begin
        for (beat_index = 0; beat_index < 2; beat_index = beat_index + 1) begin
          if ((beat_index < link_count) && !parser_error) begin
            serial_bit = (beat_index == 0) ? link_data[1] : link_data[0];
            case (state_next)
              RAW: begin
                raw_accum_next = (raw_accum_next << 1) | serial_bit;
                raw_count_next = raw_count_next + 1;
                if (raw_count_next == ADDRESS_WIDTH) begin
                  push_values[0] = raw_accum_next;
                  push_count = 1;
                  raw_accum_next = '0;
                  raw_count_next = 0;
                end
              end
              HEADER: begin
                header_accum_next = (header_accum_next << 1) | serial_bit;
                header_count_next = header_count_next + 1;
                if (header_count_next == COUNT_WIDTH) begin
                  parsed_count = header_accum_next;
                  if (parsed_count > MAX_BATCH) begin
                    parser_error = 1'b1;
                  end else if (parsed_count == 0) begin
                    state_next = RAW;
                    header_accum_next = '0;
                    header_count_next = 0;
                  end else begin
                    frame_count_next = parsed_count;
                    for (width_index = 0; width_index < ADDRESS_WIDTH;
                         width_index = width_index + 1)
                      if (((1 << (width_index + 1)) * parsed_count)
                          <= NUM_SOURCES)
                        parsed_low_width = width_index + 1;
                    low_width_next = parsed_low_width;
                    high_cursor_next = 0;
                    high_index_next = 0;
                    header_accum_next = '0;
                    header_count_next = 0;
                    state_next = HIGH;
                  end
                end
              end
              HIGH: begin
                if (!serial_bit) begin
                  high_cursor_next = high_cursor_next + 1;
                  if ((high_cursor_next << low_width_next) >= NUM_SOURCES)
                    parser_error = 1'b1;
                end else begin
                  high_values_next[high_index_next] = high_cursor_next;
                  high_index_next = high_index_next + 1;
                  if (high_index_next == frame_count_next) begin
                    if (low_width_next == 0) begin
                      for (comb_i = 0; comb_i < MAX_BATCH; comb_i = comb_i + 1)
                        if (comb_i < frame_count_next) begin
                          if ((high_values_next[comb_i] >= NUM_SOURCES) ||
                              ((comb_i > 0) &&
                               (high_values_next[comb_i] <= high_values_next[comb_i-1])))
                            parser_error = 1'b1;
                          push_values[comb_i] = high_values_next[comb_i];
                        end
                      if (!parser_error)
                        push_count = frame_count_next;
                      state_next = RAW;
                    end else begin
                      low_accum_next = '0;
                      low_count_next = 0;
                      low_index_next = 0;
                      state_next = LOW;
                    end
                  end
                end
              end
              LOW: begin
                low_accum_next = (low_accum_next << 1) | serial_bit;
                low_count_next = low_count_next + 1;
                if (low_count_next == low_width_next) begin
                  value_comb = (high_values_next[low_index_next]
                                << low_width_next) | low_accum_next;
                  if ((value_comb >= NUM_SOURCES) ||
                      ((low_index_next > 0) &&
                       (value_comb <= decoded_values_next[low_index_next-1]))) begin
                    parser_error = 1'b1;
                  end else begin
                    decoded_values_next[low_index_next] = value_comb;
                    low_index_next = low_index_next + 1;
                    low_accum_next = '0;
                    low_count_next = 0;
                    if (low_index_next == frame_count_next) begin
                      for (comb_i = 0; comb_i < MAX_BATCH; comb_i = comb_i + 1)
                        if (comb_i < frame_count_next)
                          push_values[comb_i] = decoded_values_next[comb_i];
                      push_count = frame_count_next;
                      state_next = RAW;
                    end
                  end
                end
              end
              default: parser_error = 1'b1;
            endcase

            // A completed frame/raw word must end exactly at a beat boundary.
            if ((push_count != 0 ||
                 (state == HEADER && frame_count_next == 0 && state_next == RAW))
                && (beat_index + 1 != link_count))
              parser_error = 1'b1;
          end
        end
      end
    end
    if (push_count > free_slots)
      parser_error = 1'b1;
    if (parser_error)
      push_count = 0;
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state <= RAW;
      raw_accum <= '0;
      raw_count <= 0;
      header_accum <= '0;
      header_count <= 0;
      frame_count <= 0;
      low_width_reg <= 0;
      high_cursor <= 0;
      high_index <= 0;
      low_accum <= '0;
      low_count <= 0;
      low_index <= 0;
      write_pointer <= '0;
      read_pointer <= '0;
      fifo_count <= '0;
      decode_error <= 1'b0;
      for (seq_i = 0; seq_i < MAX_BATCH; seq_i = seq_i + 1) begin
        high_values[seq_i] <= '0;
        decoded_values[seq_i] <= '0;
      end
    end else begin
      state <= state_next;
      raw_accum <= raw_accum_next;
      raw_count <= raw_count_next;
      header_accum <= header_accum_next;
      header_count <= header_count_next;
      frame_count <= frame_count_next;
      low_width_reg <= low_width_next;
      high_cursor <= high_cursor_next;
      high_index <= high_index_next;
      low_accum <= low_accum_next;
      low_count <= low_count_next;
      low_index <= low_index_next;
      for (seq_i = 0; seq_i < MAX_BATCH; seq_i = seq_i + 1) begin
        high_values[seq_i] <= high_values_next[seq_i];
        decoded_values[seq_i] <= decoded_values_next[seq_i];
      end

      if (parser_error) begin
        decode_error <= 1'b1;
        state <= RAW;
        raw_accum <= '0;
        raw_count <= 0;
      end

      for (seq_i = 0; seq_i < MAX_BATCH; seq_i = seq_i + 1)
        if (seq_i < push_count)
          fifo_memory[(write_pointer + seq_i) % FIFO_DEPTH] <= push_values[seq_i];
      if (push_count != 0)
        write_pointer <= write_pointer + FIFO_PTR_WIDTH'(push_count);
      if (pop_event)
        read_pointer <= read_pointer + 1'b1;
      case ({push_count != 0, pop_event})
        2'b10: fifo_count <= fifo_count + FIFO_COUNT_WIDTH'(push_count);
        2'b01: fifo_count <= fifo_count - 1'b1;
        2'b11: fifo_count <= fifo_count + FIFO_COUNT_WIDTH'(push_count) - 1'b1;
        default: fifo_count <= fifo_count;
      endcase
    end
  end
endmodule
