`timescale 1ns/1ps

// Candidate-specific protocol properties for head-owned Xcelium qualification.
// This file is simulation-only and is not part of the synthesis file list.
module a4_quadtree_node_assertions #(
  parameter int RADIX = 4,
  parameter int ADDR_WIDTH = 16,
  parameter int SOURCE_WIDTH = 4,
  parameter int AGE_WIDTH = 8
) (
  input logic clk,
  input logic rst_n,
  input logic [RADIX-1:0] child_ready,
  input logic out_valid,
  input logic out_ready,
  input logic [ADDR_WIDTH-1:0] out_event,
  input logic [SOURCE_WIDTH-1:0] out_source,
  input logic [AGE_WIDTH-1:0] out_age
);
  default clocking cb @(posedge clk); endclocking
  default disable iff (!rst_n);

  ready_is_onehot: assert property ($onehot0(child_ready));
  full_stall_accepts_nothing: assert property (
    out_valid && !out_ready |-> child_ready == '0);
  stalled_output_is_stable: assert property (
    out_valid && !out_ready |=>
      out_valid && $stable({out_event, out_source, out_age}));
endmodule

bind a4_quadtree_node a4_quadtree_node_assertions #(
  .RADIX(RADIX),
  .ADDR_WIDTH(ADDR_WIDTH),
  .SOURCE_WIDTH(SOURCE_WIDTH),
  .AGE_WIDTH(AGE_WIDTH)
) a4_bound_properties (
  .clk(clk),
  .rst_n(rst_n),
  .child_ready(child_ready),
  .out_valid(out_valid),
  .out_ready(out_ready),
  .out_event(out_event),
  .out_source(out_source),
  .out_age(out_age)
);
