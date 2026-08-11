`timescale 1ns/1ps

module a6_ef_batch_encoder #(
  parameter int NUM_SOURCES = 16,
  parameter int MAX_BATCH = 16,
  parameter int ADDRESS_WIDTH = $clog2(NUM_SOURCES),
  parameter int COUNT_WIDTH = $clog2(MAX_BATCH + 1),
  parameter int MAX_STREAM_BITS = COUNT_WIDTH + NUM_SOURCES
                                + MAX_BATCH * ADDRESS_WIDTH
) (
  input  logic                                 clk,
  input  logic                                 rst_n,
  input  logic                                 batch_valid,
  output logic                                 batch_ready,
  input  logic [COUNT_WIDTH-1:0]               batch_count,
  input  logic [MAX_BATCH*ADDRESS_WIDTH-1:0]   batch_sources,
  output logic [1:0]                           link_count,
  output logic [1:0]                           link_data,
  input  logic                                 link_ready,
  output logic                                 encode_error,
  output logic                                 encoded_ef_observe
);
  typedef enum logic [1:0] {IDLE, SEND_MARKER, SEND_BITS} state_t;
  state_t state;

  logic [MAX_STREAM_BITS-1:0] candidate_raw;
  logic [MAX_STREAM_BITS-1:0] candidate_ef;
  logic [MAX_STREAM_BITS-1:0] send_shift;
  integer raw_length_comb;
  integer ef_length_comb;
  integer raw_cycles_comb;
  integer ef_cycles_comb;
  integer send_length;
  integer low_width_comb;
  integer cursor;
  integer high_value;
  integer previous_high;
  integer source_value;
  integer previous_source;
  integer i;
  integer bit_index;
  integer zero_index;
  integer width_index;
  logic input_error_comb;

  initial begin
    if ((NUM_SOURCES < 2) || ((NUM_SOURCES & (NUM_SOURCES-1)) != 0))
      $error("A6 EF encoder requires a power-of-two source universe");
    if ((MAX_BATCH < 1) || (MAX_BATCH > NUM_SOURCES))
      $error("A6 EF encoder has an invalid MAX_BATCH");
    if ((ADDRESS_WIDTH % 2) != 0)
      $error("A6 EF raw escape requires an even address width on the 2-bit link");
  end

  always_comb begin
    candidate_raw = '0;
    candidate_ef = '0;
    raw_length_comb = batch_count * ADDRESS_WIDTH;
    raw_cycles_comb = (raw_length_comb + 1) / 2;
    low_width_comb = 0;
    i = 0;
    bit_index = 0;
    zero_index = 0;
    width_index = 0;
    high_value = 0;
    source_value = 0;
    for (width_index = 0; width_index < ADDRESS_WIDTH;
         width_index = width_index + 1)
      if ((batch_count != 0) &&
          (((1 << (width_index + 1)) * batch_count) <= NUM_SOURCES))
        low_width_comb = width_index + 1;
    cursor = 0;
    input_error_comb = (batch_count > MAX_BATCH);
    previous_source = -1;

    for (i = 0; i < MAX_BATCH; i = i + 1) begin
      source_value = batch_sources[i*ADDRESS_WIDTH +: ADDRESS_WIDTH];
      if (i < batch_count) begin
        if ((source_value >= NUM_SOURCES) ||
            ((previous_source >= 0) && (source_value <= previous_source)))
          input_error_comb = 1'b1;
        previous_source = source_value;
        for (bit_index = 0; bit_index < ADDRESS_WIDTH; bit_index = bit_index + 1)
          candidate_raw[i*ADDRESS_WIDTH+bit_index] =
            source_value[ADDRESS_WIDTH-1-bit_index];
      end
    end

    // Count header, most-significant bit first.  The marker is a separate
    // one-bit beat and is not stored in candidate_ef.
    for (bit_index = 0; bit_index < COUNT_WIDTH; bit_index = bit_index + 1)
      candidate_ef[cursor+bit_index] = batch_count[COUNT_WIDTH-1-bit_index];
    cursor = cursor + COUNT_WIDTH;

    previous_high = 0;
    for (i = 0; i < MAX_BATCH; i = i + 1) begin
      source_value = batch_sources[i*ADDRESS_WIDTH +: ADDRESS_WIDTH];
      high_value = source_value >> low_width_comb;
      if (i < batch_count) begin
        for (zero_index = 0; zero_index < NUM_SOURCES; zero_index = zero_index + 1)
          if (zero_index < (high_value - previous_high)) begin
            candidate_ef[cursor] = 1'b0;
            cursor = cursor + 1;
          end
        candidate_ef[cursor] = 1'b1;
        cursor = cursor + 1;
        previous_high = high_value;
      end
    end

    for (i = 0; i < MAX_BATCH; i = i + 1) begin
      source_value = batch_sources[i*ADDRESS_WIDTH +: ADDRESS_WIDTH];
      if (i < batch_count)
        for (bit_index = 0; bit_index < ADDRESS_WIDTH; bit_index = bit_index + 1)
          if (bit_index < low_width_comb) begin
            candidate_ef[cursor] = source_value[low_width_comb-1-bit_index];
            cursor = cursor + 1;
          end
    end
    ef_length_comb = cursor;
    ef_cycles_comb = 1 + ((ef_length_comb + 1) / 2);

    batch_ready = (state == IDLE) && !encode_error;
    link_count = 2'd0;
    link_data = 2'b00;
    if (state == SEND_MARKER) begin
      link_count = 2'd1;
      link_data[1] = 1'b1;
    end else if (state == SEND_BITS) begin
      link_count = (send_length == 1) ? 2'd1 : 2'd2;
      link_data[1] = send_shift[0];
      if (send_length > 1)
        link_data[0] = send_shift[1];
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state <= IDLE;
      send_shift <= '0;
      send_length <= 0;
      encode_error <= 1'b0;
      encoded_ef_observe <= 1'b0;
    end else begin
      case (state)
        IDLE: begin
          encoded_ef_observe <= 1'b0;
          if (batch_valid && batch_ready) begin
            if (input_error_comb) begin
              encode_error <= 1'b1;
            end else if ((batch_count == 0) ||
                         (ef_cycles_comb < raw_cycles_comb)) begin
              send_shift <= candidate_ef;
              send_length <= ef_length_comb;
              encoded_ef_observe <= 1'b1;
              state <= SEND_MARKER;
            end else begin
              send_shift <= candidate_raw;
              send_length <= raw_length_comb;
              state <= SEND_BITS;
            end
          end
        end
        SEND_MARKER: begin
          if (link_ready)
            state <= SEND_BITS;
        end
        SEND_BITS: begin
          if (link_ready) begin
            if (send_length <= 2) begin
              send_shift <= '0;
              send_length <= 0;
              state <= IDLE;
            end else begin
              send_shift <= send_shift >> 2;
              send_length <= send_length - 2;
            end
          end
        end
        default: begin
          encode_error <= 1'b1;
          state <= IDLE;
        end
      endcase
    end
  end
endmodule
