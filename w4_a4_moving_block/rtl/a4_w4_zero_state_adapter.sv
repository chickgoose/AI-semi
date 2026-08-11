`timescale 1ns/1ps

module a4_w4_zero_state_adapter #(
  parameter int MAX_ADVANCE = 2
) (
  input  logic        clk,
  input  logic        rst_n,
  input  logic [15:0] source_valid,
  output logic [15:0] source_ready,
  output logic        retire_valid,
  input  logic        retire_ready,
  output logic [3:0]  retire_address,
  output logic [31:0] raw_retire_event
);
  wire [31:0] native_source_event [16];
  wire [3:0] native_retire_source;

  for (genvar source = 0; source < 16; source++) begin : g_address_only
    assign native_source_event[source] = 32'(source);
  end

  assign retire_address = native_retire_source;

  a4_moving_block_tree #(
    .NUM_SOURCES(16),
    .ADDR_WIDTH(32),
    .MAX_ADVANCE(MAX_ADVANCE)
  ) pinned_a4_rtl (
    .clk(clk),
    .rst_n(rst_n),
    .source_valid(source_valid),
    .source_ready(source_ready),
    .source_event(native_source_event),
    .retire_valid(retire_valid),
    .retire_ready(retire_ready),
    .retire_event(raw_retire_event),
    .retire_source(native_retire_source)
  );
endmodule
