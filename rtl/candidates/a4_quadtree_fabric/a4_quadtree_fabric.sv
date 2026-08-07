`timescale 1ns/1ps

module a4_quadtree_fabric #(
  parameter int NUM_SOURCES  = 16,
  parameter int ADDR_WIDTH   = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES),
  parameter int AGE_WIDTH    = 8
) (
  input  logic                         clk,
  input  logic                         rst_n,
  input  logic [NUM_SOURCES-1:0]       source_valid,
  output logic [NUM_SOURCES-1:0]       source_ready,
  input  logic [ADDR_WIDTH-1:0]        source_event [NUM_SOURCES],
  output logic                         retire_valid,
  input  logic                         retire_ready,
  output logic [ADDR_WIDTH-1:0]        retire_event,
  output logic [SOURCE_WIDTH-1:0]      retire_source
);
  localparam int RADIX = 4;
  localparam int LEAF_COUNT = 4;

  logic [RADIX-1:0] q0_valid, q0_ready;
  logic [RADIX-1:0] q1_valid, q1_ready;
  logic [RADIX-1:0] q2_valid, q2_ready;
  logic [RADIX-1:0] q3_valid, q3_ready;
  logic [ADDR_WIDTH-1:0] q0_event [RADIX];
  logic [ADDR_WIDTH-1:0] q1_event [RADIX];
  logic [ADDR_WIDTH-1:0] q2_event [RADIX];
  logic [ADDR_WIDTH-1:0] q3_event [RADIX];
  logic [SOURCE_WIDTH-1:0] q0_source [RADIX];
  logic [SOURCE_WIDTH-1:0] q1_source [RADIX];
  logic [SOURCE_WIDTH-1:0] q2_source [RADIX];
  logic [SOURCE_WIDTH-1:0] q3_source [RADIX];
  logic [AGE_WIDTH-1:0] q0_age [RADIX];
  logic [AGE_WIDTH-1:0] q1_age [RADIX];
  logic [AGE_WIDTH-1:0] q2_age [RADIX];
  logic [AGE_WIDTH-1:0] q3_age [RADIX];

  logic [LEAF_COUNT-1:0] leaf_valid;
  logic [LEAF_COUNT-1:0] leaf_ready;
  logic [ADDR_WIDTH-1:0] leaf_event [LEAF_COUNT];
  logic [SOURCE_WIDTH-1:0] leaf_source [LEAF_COUNT];
  logic [AGE_WIDTH-1:0] leaf_age [LEAF_COUNT];
  logic [AGE_WIDTH-1:0] root_age_unused;

  // Child order is NW, NE, SW, SE within each 2x2 quadrant.
  assign q0_valid = {source_valid[5], source_valid[4], source_valid[1], source_valid[0]};
  assign q1_valid = {source_valid[7], source_valid[6], source_valid[3], source_valid[2]};
  assign q2_valid = {source_valid[13], source_valid[12], source_valid[9], source_valid[8]};
  assign q3_valid = {source_valid[15], source_valid[14], source_valid[11], source_valid[10]};
  assign source_ready[0] = q0_ready[0];
  assign source_ready[1] = q0_ready[1];
  assign source_ready[4] = q0_ready[2];
  assign source_ready[5] = q0_ready[3];
  assign source_ready[2] = q1_ready[0];
  assign source_ready[3] = q1_ready[1];
  assign source_ready[6] = q1_ready[2];
  assign source_ready[7] = q1_ready[3];
  assign source_ready[8] = q2_ready[0];
  assign source_ready[9] = q2_ready[1];
  assign source_ready[12] = q2_ready[2];
  assign source_ready[13] = q2_ready[3];
  assign source_ready[10] = q3_ready[0];
  assign source_ready[11] = q3_ready[1];
  assign source_ready[14] = q3_ready[2];
  assign source_ready[15] = q3_ready[3];

  genvar child;
  generate
    for (child = 0; child < RADIX; child = child + 1) begin : gen_child_fields
      assign q0_age[child] = '0;
      assign q1_age[child] = '0;
      assign q2_age[child] = '0;
      assign q3_age[child] = '0;
    end
  endgenerate

  assign q0_event[0] = source_event[0];
  assign q0_event[1] = source_event[1];
  assign q0_event[2] = source_event[4];
  assign q0_event[3] = source_event[5];
  assign q1_event[0] = source_event[2];
  assign q1_event[1] = source_event[3];
  assign q1_event[2] = source_event[6];
  assign q1_event[3] = source_event[7];
  assign q2_event[0] = source_event[8];
  assign q2_event[1] = source_event[9];
  assign q2_event[2] = source_event[12];
  assign q2_event[3] = source_event[13];
  assign q3_event[0] = source_event[10];
  assign q3_event[1] = source_event[11];
  assign q3_event[2] = source_event[14];
  assign q3_event[3] = source_event[15];

  assign q0_source[0] = SOURCE_WIDTH'(0);
  assign q0_source[1] = SOURCE_WIDTH'(1);
  assign q0_source[2] = SOURCE_WIDTH'(4);
  assign q0_source[3] = SOURCE_WIDTH'(5);
  assign q1_source[0] = SOURCE_WIDTH'(2);
  assign q1_source[1] = SOURCE_WIDTH'(3);
  assign q1_source[2] = SOURCE_WIDTH'(6);
  assign q1_source[3] = SOURCE_WIDTH'(7);
  assign q2_source[0] = SOURCE_WIDTH'(8);
  assign q2_source[1] = SOURCE_WIDTH'(9);
  assign q2_source[2] = SOURCE_WIDTH'(12);
  assign q2_source[3] = SOURCE_WIDTH'(13);
  assign q3_source[0] = SOURCE_WIDTH'(10);
  assign q3_source[1] = SOURCE_WIDTH'(11);
  assign q3_source[2] = SOURCE_WIDTH'(14);
  assign q3_source[3] = SOURCE_WIDTH'(15);

  a4_quadtree_node #(.RADIX(RADIX), .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH), .AGE_WIDTH(AGE_WIDTH)) leaf_node_q0 (
    .clk(clk), .rst_n(rst_n), .child_valid(q0_valid), .child_ready(q0_ready),
    .child_event(q0_event), .child_source(q0_source), .child_age(q0_age),
    .out_valid(leaf_valid[0]), .out_ready(leaf_ready[0]),
    .out_event(leaf_event[0]), .out_source(leaf_source[0]), .out_age(leaf_age[0]));

  a4_quadtree_node #(.RADIX(RADIX), .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH), .AGE_WIDTH(AGE_WIDTH)) leaf_node_q1 (
    .clk(clk), .rst_n(rst_n), .child_valid(q1_valid), .child_ready(q1_ready),
    .child_event(q1_event), .child_source(q1_source), .child_age(q1_age),
    .out_valid(leaf_valid[1]), .out_ready(leaf_ready[1]),
    .out_event(leaf_event[1]), .out_source(leaf_source[1]), .out_age(leaf_age[1]));

  a4_quadtree_node #(.RADIX(RADIX), .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH), .AGE_WIDTH(AGE_WIDTH)) leaf_node_q2 (
    .clk(clk), .rst_n(rst_n), .child_valid(q2_valid), .child_ready(q2_ready),
    .child_event(q2_event), .child_source(q2_source), .child_age(q2_age),
    .out_valid(leaf_valid[2]), .out_ready(leaf_ready[2]),
    .out_event(leaf_event[2]), .out_source(leaf_source[2]), .out_age(leaf_age[2]));

  a4_quadtree_node #(.RADIX(RADIX), .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH), .AGE_WIDTH(AGE_WIDTH)) leaf_node_q3 (
    .clk(clk), .rst_n(rst_n), .child_valid(q3_valid), .child_ready(q3_ready),
    .child_event(q3_event), .child_source(q3_source), .child_age(q3_age),
    .out_valid(leaf_valid[3]), .out_ready(leaf_ready[3]),
    .out_event(leaf_event[3]), .out_source(leaf_source[3]), .out_age(leaf_age[3]));

  a4_quadtree_node #(
    .RADIX(RADIX),
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH),
    .AGE_WIDTH(AGE_WIDTH)
  ) root_node (
    .clk(clk),
    .rst_n(rst_n),
    .child_valid(leaf_valid),
    .child_ready(leaf_ready),
    .child_event(leaf_event),
    .child_source(leaf_source),
    .child_age(leaf_age),
    .out_valid(retire_valid),
    .out_ready(retire_ready),
    .out_event(retire_event),
    .out_source(retire_source),
    .out_age(root_age_unused)
  );

`ifndef SYNTHESIS
  initial begin
    if (NUM_SOURCES != 16)
      $fatal(1, "A4 frozen candidate requires NUM_SOURCES=16");
  end
`endif
endmodule
