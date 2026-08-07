`timescale 1ns/1ps

// Optional N=64 timing experiment only.  The instantiated shell is exactly the
// registered phase-4 comparison boundary; this is not a functional binding or
// an N=16 default shortlist.
module a9_static_n64_timing_top (
  input  logic          clk_i,
  input  logic          rst_ni,
  input  logic [63:0]   source_valid_i,
  output logic [63:0]   source_ready_o,
  input  logic [1023:0] source_event_i,
  output logic [7:0]    retire_valid_o,
  input  logic [7:0]    retire_ready_i,
  output logic [127:0]  retire_event_o,
  output logic [47:0]   retire_source_o
);
  localparam int NUM_SOURCES = 64;
  localparam int ADDR_WIDTH = 16;
  localparam int RETIRE_LANES = 8;
  localparam int SOURCE_WIDTH = 6;

  logic [NUM_SOURCES-1:0][ADDR_WIDTH-1:0] source_event_native;
  logic [RETIRE_LANES-1:0][ADDR_WIDTH-1:0] retire_event_native;
  logic [RETIRE_LANES-1:0][SOURCE_WIDTH-1:0] retire_source_native;

  genvar source;
  generate
    for (source = 0; source < NUM_SOURCES; source = source + 1) begin : unpack_source
      assign source_event_native[source] =
        source_event_i[source*ADDR_WIDTH +: ADDR_WIDTH];
    end
  endgenerate

  genvar lane;
  generate
    for (lane = 0; lane < RETIRE_LANES; lane = lane + 1) begin : pack_retire
      assign retire_event_o[lane*ADDR_WIDTH +: ADDR_WIDTH] =
        retire_event_native[lane];
      assign retire_source_o[lane*SOURCE_WIDTH +: SOURCE_WIDTH] =
        retire_source_native[lane];
    end
  endgenerate

  a9_phase4_synth_top #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(RETIRE_LANES),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) u_phase4_boundary (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .source_valid_i(source_valid_i),
    .source_ready_o(source_ready_o),
    .source_event_i(source_event_native),
    .retire_valid_o(retire_valid_o),
    .retire_ready_i(retire_ready_i),
    .retire_event_o(retire_event_native),
    .retire_source_o(retire_source_native)
  );
endmodule
