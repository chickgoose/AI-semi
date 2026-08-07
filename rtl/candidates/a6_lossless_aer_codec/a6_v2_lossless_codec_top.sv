`timescale 1ns/1ps
module a6_v2_lossless_codec_top #(
  parameter int NUM_SOURCES = 16,
  parameter int EVENT_WIDTH = 6,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (
  input  logic                    clk,
  input  logic                    rst_n,
  input  logic [NUM_SOURCES-1:0]  source_valid,
  output logic [NUM_SOURCES-1:0]  source_ready,
  output logic                    retire_valid,
  input  logic                    retire_ready,
  output logic [EVENT_WIDTH-1:0]  retire_event,
  output logic [SOURCE_WIDTH-1:0] retire_source,
  output logic [1:0]              link_count_observe,
  output logic [1:0]              link_data_observe,
  output logic                    link_ready_observe,
  output logic                    decode_error_observe
);
  logic [SOURCE_WIDTH-1:0] rr_start;
  logic selected_valid;
  logic [SOURCE_WIDTH-1:0] selected_source;
  logic encoder_ready;
  logic [1:0] link_count;
  logic [1:0] link_data;
  logic link_ready;
  logic decoder_valid;
  logic [SOURCE_WIDTH-1:0] decoder_source;
  integer offset;
  integer candidate_source;
  integer selected_integer;

  always_comb begin
    selected_integer = -1;
    for (offset = 0; offset < NUM_SOURCES; offset = offset + 1) begin
      candidate_source = rr_start + offset;
      if (candidate_source >= NUM_SOURCES)
        candidate_source = candidate_source - NUM_SOURCES;
      if ((selected_integer < 0) && source_valid[candidate_source])
        selected_integer = candidate_source;
    end
    selected_valid = (selected_integer >= 0);
    selected_source = SOURCE_WIDTH'(selected_integer);
  end

  always_comb begin
    source_ready = '0;
    if (selected_valid && encoder_ready)
      source_ready[selected_source] = 1'b1;
    retire_valid = decoder_valid;
    retire_source = decoder_source;
    retire_event = '0;
    retire_event[1] = 1'b1;
    retire_event[SOURCE_WIDTH+1:2] = decoder_source;
    link_count_observe = link_count;
    link_data_observe = link_data;
    link_ready_observe = link_ready;
  end

  a6_v2_block_encoder encoder (
    .clk(clk), .rst_n(rst_n),
    .event_valid(selected_valid), .event_ready(encoder_ready),
    .event_address(selected_source), .link_count(link_count),
    .link_data(link_data), .link_ready(link_ready)
  );

  a6_v2_block_decoder decoder (
    .clk(clk), .rst_n(rst_n), .link_count(link_count), .link_data(link_data),
    .link_ready(link_ready), .event_valid(decoder_valid),
    .event_address(decoder_source), .event_ready(retire_ready),
    .decode_error(decode_error_observe)
  );

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)
      rr_start <= '0;
    else if (selected_valid && encoder_ready) begin
      if (selected_source == SOURCE_WIDTH'(NUM_SOURCES-1))
        rr_start <= '0;
      else
        rr_start <= selected_source + 1'b1;
    end
  end
endmodule

