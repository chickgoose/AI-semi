`timescale 1ns/1ps

module a7_prefix_structural_top #(
  parameter int N = 16,
  parameter int K = 4,
  parameter int AW = 16,
  parameter int SW = (N <= 1) ? 1 : $clog2(N)
) (
  input logic clk, rst_n,
  input logic [N-1:0] source_valid,
  input logic [N*AW-1:0] source_event_flat,
  input logic [K-1:0] retire_ready,
  output logic [N-1:0] source_ready,
  output logic [K-1:0] retire_valid,
  output logic [K*AW-1:0] retire_event_flat,
  output logic [K*SW-1:0] retire_source_flat
);
  logic [N-1:0][AW-1:0] source_event;
  logic [K-1:0][AW-1:0] retire_event;
  logic [K-1:0][SW-1:0] retire_source;
  genvar i;
  generate
    for (i=0; i<N; i=i+1)
      assign source_event[i] = source_event_flat[i*AW +: AW];
    for (i=0; i<K; i=i+1) begin
      assign retire_event_flat[i*AW +: AW] = retire_event[i];
      assign retire_source_flat[i*SW +: SW] = retire_source[i];
    end
  endgenerate
  a7_parallel_event_compactor #(
    .NUM_SOURCES(N), .ADDR_WIDTH(AW), .RETIRE_LANES(K), .SOURCE_WIDTH(SW)
  ) dut (.*);
endmodule

module a7_replicated_structural_top #(
  parameter int N = 16,
  parameter int K = 4,
  parameter int AW = 16,
  parameter int SW = (N <= 1) ? 1 : $clog2(N)
) (
  input logic clk, rst_n,
  input logic [N-1:0] source_valid,
  input logic [N*AW-1:0] source_event_flat,
  input logic [K-1:0] retire_ready,
  output logic [N-1:0] source_ready,
  output logic [K-1:0] retire_valid,
  output logic [K*AW-1:0] retire_event_flat,
  output logic [K*SW-1:0] retire_source_flat
);
  logic [N-1:0][AW-1:0] source_event;
  logic [K-1:0][AW-1:0] retire_event;
  logic [K-1:0][SW-1:0] retire_source;
  genvar i;
  generate
    for (i=0; i<N; i=i+1)
      assign source_event[i] = source_event_flat[i*AW +: AW];
    for (i=0; i<K; i=i+1) begin
      assign retire_event_flat[i*AW +: AW] = retire_event[i];
      assign retire_source_flat[i*SW +: SW] = retire_source[i];
    end
  endgenerate
  a7_replicated_selector_reference #(
    .NUM_SOURCES(N), .ADDR_WIDTH(AW), .RETIRE_LANES(K), .SOURCE_WIDTH(SW)
  ) dut (.*);
endmodule
