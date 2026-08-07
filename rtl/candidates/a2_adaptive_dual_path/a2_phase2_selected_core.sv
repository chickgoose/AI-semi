`timescale 1ns/1ps

// Exact phase-2 shortlist wrapper. NUM_SOURCES and ADDR_WIDTH remain the first
// two parameters so the frozen common Genus driver can elaborate it unchanged.
module a2_phase2_selected_core #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (
  input  logic clk_i,
  input  logic rst_ni,
  input  logic [NUM_SOURCES-1:0] source_valid_i,
  output logic [NUM_SOURCES-1:0] source_ready_o,
  input  logic [ADDR_WIDTH-1:0] source_event_i [NUM_SOURCES],
  output logic retire_valid_o,
  input  logic retire_ready_i,
  output logic [ADDR_WIDTH-1:0] retire_event_o,
  output logic [SOURCE_WIDTH-1:0] retire_source_o
);
  a2_adaptive_dual_path_core #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RESERVOIR_DEPTH(16),
    .BANK_COUNT(4),
    .ENTER_LEVEL(4),
    .EXIT_LEVEL(0),
    .QUIET_CYCLES(1)
  ) selected (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .source_valid_i(source_valid_i),
    .source_ready_o(source_ready_o),
    .source_event_i(source_event_i),
    .retire_valid_o(retire_valid_o),
    .retire_ready_i(retire_ready_i),
    .retire_event_o(retire_event_o),
    .retire_source_o(retire_source_o)
  );
endmodule
