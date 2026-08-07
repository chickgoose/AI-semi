`timescale 1ns/1ps

// Candidate-only structural comparison. Both tops contain identical one-entry
// ingress state, payload widths, and a registered ready/valid retire boundary.
// Only the arbitration/transport topology differs.

module a4_struct_rr_node #(
  parameter int RADIX = 4,
  parameter int PAYLOAD_WIDTH = 28,
  parameter int AGE_WIDTH = 8,
  parameter int PHASE_WIDTH = (RADIX <= 1) ? 1 : $clog2(RADIX)
) (
  input  logic                             clk,
  input  logic                             rst_n,
  input  logic [RADIX-1:0]                 child_valid,
  output logic [RADIX-1:0]                 child_ready,
  input  logic [RADIX*PAYLOAD_WIDTH-1:0]   child_payload,
  output logic                             out_valid,
  input  logic                             out_ready,
  output logic [PAYLOAD_WIDTH-1:0]         out_payload
);
  logic slot_valid;
  logic [PAYLOAD_WIDTH-1:0] slot_payload;
  logic [PHASE_WIDTH-1:0] rr_phase;
  logic slot_available;
  integer offset;
  integer candidate_child;
  integer selected_child;

  always_comb begin
    out_valid = slot_valid;
    out_payload = slot_payload;
    slot_available = !slot_valid || out_ready;
    selected_child = -1;
    for (offset = 0; offset < RADIX; offset = offset + 1) begin
      candidate_child = int'(rr_phase) + offset;
      if (candidate_child >= RADIX)
        candidate_child = candidate_child - RADIX;
      if ((selected_child < 0) && child_valid[candidate_child])
        selected_child = candidate_child;
    end
    child_ready = '0;
    if (slot_available && (selected_child >= 0))
      child_ready[selected_child] = 1'b1;
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      slot_valid <= 1'b0;
      slot_payload <= '0;
      rr_phase <= '0;
    end else if (slot_available) begin
      if (selected_child >= 0) begin
        slot_valid <= 1'b1;
        slot_payload <= child_payload[selected_child*PAYLOAD_WIDTH +: PAYLOAD_WIDTH];
        if (&child_payload[selected_child*PAYLOAD_WIDTH +: AGE_WIDTH])
          slot_payload[AGE_WIDTH-1:0] <= child_payload[
            selected_child*PAYLOAD_WIDTH +: AGE_WIDTH];
        else
          slot_payload[AGE_WIDTH-1:0] <= child_payload[
            selected_child*PAYLOAD_WIDTH +: AGE_WIDTH] + 1'b1;
        if (selected_child == RADIX-1)
          rr_phase <= '0;
        else
          rr_phase <= PHASE_WIDTH'(selected_child + 1);
      end else begin
        slot_valid <= 1'b0;
        slot_payload <= '0;
      end
    end
  end
endmodule


module a4_struct_tree #(
  parameter int NUM_INPUTS = 16,
  parameter int PAYLOAD_WIDTH = 28,
  parameter int AGE_WIDTH = 8
) (
  input  logic                                clk,
  input  logic                                rst_n,
  input  logic [NUM_INPUTS-1:0]               in_valid,
  output logic [NUM_INPUTS-1:0]               in_ready,
  input  logic [NUM_INPUTS*PAYLOAD_WIDTH-1:0] in_payload,
  output logic                                out_valid,
  input  logic                                out_ready,
  output logic [PAYLOAD_WIDTH-1:0]            out_payload
);
  generate
    if (NUM_INPUTS == 4) begin : gen_leaf
      a4_struct_rr_node #(
        .RADIX(4), .PAYLOAD_WIDTH(PAYLOAD_WIDTH), .AGE_WIDTH(AGE_WIDTH)
      ) node (
        .clk(clk), .rst_n(rst_n), .child_valid(in_valid),
        .child_ready(in_ready), .child_payload(in_payload),
        .out_valid(out_valid), .out_ready(out_ready), .out_payload(out_payload)
      );
    end else begin : gen_branch
      localparam int CHILD_INPUTS = NUM_INPUTS / 4;
      logic [3:0] child_valid;
      logic [3:0] child_ready;
      logic [4*PAYLOAD_WIDTH-1:0] child_payload;
      genvar branch;
      for (branch = 0; branch < 4; branch = branch + 1) begin : gen_subtree
        a4_struct_tree #(
          .NUM_INPUTS(CHILD_INPUTS), .PAYLOAD_WIDTH(PAYLOAD_WIDTH),
          .AGE_WIDTH(AGE_WIDTH)
        ) subtree (
          .clk(clk), .rst_n(rst_n),
          .in_valid(in_valid[branch*CHILD_INPUTS +: CHILD_INPUTS]),
          .in_ready(in_ready[branch*CHILD_INPUTS +: CHILD_INPUTS]),
          .in_payload(in_payload[
            branch*CHILD_INPUTS*PAYLOAD_WIDTH +: CHILD_INPUTS*PAYLOAD_WIDTH]),
          .out_valid(child_valid[branch]), .out_ready(child_ready[branch]),
          .out_payload(child_payload[branch*PAYLOAD_WIDTH +: PAYLOAD_WIDTH])
        );
      end
      a4_struct_rr_node #(
        .RADIX(4), .PAYLOAD_WIDTH(PAYLOAD_WIDTH), .AGE_WIDTH(AGE_WIDTH)
      ) node (
        .clk(clk), .rst_n(rst_n), .child_valid(child_valid),
        .child_ready(child_ready), .child_payload(child_payload),
        .out_valid(out_valid), .out_ready(out_ready), .out_payload(out_payload)
      );
    end
  endgenerate
endmodule


module a4_structural_candidate #(
  parameter int NUM_SOURCES = 16,
  parameter int EVENT_WIDTH = 16,
  parameter int AGE_WIDTH = 8,
  parameter bit USE_TREE = 1'b1,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES),
  parameter int PAYLOAD_WIDTH = EVENT_WIDTH + SOURCE_WIDTH + AGE_WIDTH
) (
  input  logic                               clk,
  input  logic                               rst_n,
  input  logic [NUM_SOURCES-1:0]             source_valid,
  output logic [NUM_SOURCES-1:0]             source_ready,
  input  logic [NUM_SOURCES*EVENT_WIDTH-1:0] source_event,
  output logic                               retire_valid,
  input  logic                               retire_ready,
  output logic [EVENT_WIDTH-1:0]             retire_event,
  output logic [SOURCE_WIDTH-1:0]            retire_source,
  output logic [AGE_WIDTH-1:0]               retire_age
);
  logic [NUM_SOURCES-1:0] ingress_valid;
  logic [NUM_SOURCES*EVENT_WIDTH-1:0] ingress_event;
  logic [NUM_SOURCES-1:0] core_ready;
  logic [NUM_SOURCES*PAYLOAD_WIDTH-1:0] core_payload;
  logic [PAYLOAD_WIDTH-1:0] retire_payload;
  genvar source;

  generate
    for (source = 0; source < NUM_SOURCES; source = source + 1) begin : gen_ingress
      always_comb begin
        source_ready[source] = !ingress_valid[source] || core_ready[source];
        core_payload[source*PAYLOAD_WIDTH +: PAYLOAD_WIDTH] = {
          ingress_event[source*EVENT_WIDTH +: EVENT_WIDTH],
          SOURCE_WIDTH'(source),
          {AGE_WIDTH{1'b0}}
        };
      end
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          ingress_valid[source] <= 1'b0;
          ingress_event[source*EVENT_WIDTH +: EVENT_WIDTH] <= '0;
        end else if (source_ready[source]) begin
          ingress_valid[source] <= source_valid[source];
          if (source_valid[source])
            ingress_event[source*EVENT_WIDTH +: EVENT_WIDTH] <=
              source_event[source*EVENT_WIDTH +: EVENT_WIDTH];
          else
            ingress_event[source*EVENT_WIDTH +: EVENT_WIDTH] <= '0;
        end
      end
    end

    if (USE_TREE) begin : gen_tree
      a4_struct_tree #(
        .NUM_INPUTS(NUM_SOURCES), .PAYLOAD_WIDTH(PAYLOAD_WIDTH),
        .AGE_WIDTH(AGE_WIDTH)
      ) core (
        .clk(clk), .rst_n(rst_n), .in_valid(ingress_valid),
        .in_ready(core_ready), .in_payload(core_payload),
        .out_valid(retire_valid), .out_ready(retire_ready),
        .out_payload(retire_payload)
      );
    end else begin : gen_flat
      a4_struct_rr_node #(
        .RADIX(NUM_SOURCES), .PAYLOAD_WIDTH(PAYLOAD_WIDTH), .AGE_WIDTH(AGE_WIDTH)
      ) core (
        .clk(clk), .rst_n(rst_n), .child_valid(ingress_valid),
        .child_ready(core_ready), .child_payload(core_payload),
        .out_valid(retire_valid), .out_ready(retire_ready),
        .out_payload(retire_payload)
      );
    end
  endgenerate

  assign retire_age = retire_payload[AGE_WIDTH-1:0];
  assign retire_source = retire_payload[AGE_WIDTH +: SOURCE_WIDTH];
  assign retire_event = retire_payload[AGE_WIDTH+SOURCE_WIDTH +: EVENT_WIDTH];

`ifndef SYNTHESIS
  initial begin
    if ((NUM_SOURCES != 16) && (NUM_SOURCES != 64))
      $fatal(1, "A4 structural gate supports N=16 or N=64");
  end
`endif
endmodule


module a4_struct_quadtree_top #(
  parameter int NUM_SOURCES = 16,
  parameter int EVENT_WIDTH = 16,
  parameter int AGE_WIDTH = 8,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (
  input logic clk, input logic rst_n,
  input logic [NUM_SOURCES-1:0] source_valid,
  output logic [NUM_SOURCES-1:0] source_ready,
  input logic [NUM_SOURCES*EVENT_WIDTH-1:0] source_event,
  output logic retire_valid, input logic retire_ready,
  output logic [EVENT_WIDTH-1:0] retire_event,
  output logic [SOURCE_WIDTH-1:0] retire_source,
  output logic [AGE_WIDTH-1:0] retire_age
);
  a4_structural_candidate #(
    .NUM_SOURCES(NUM_SOURCES), .EVENT_WIDTH(EVENT_WIDTH),
    .AGE_WIDTH(AGE_WIDTH), .USE_TREE(1'b1)
  ) candidate (.*);
endmodule


module a4_struct_flat_top #(
  parameter int NUM_SOURCES = 16,
  parameter int EVENT_WIDTH = 16,
  parameter int AGE_WIDTH = 8,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (
  input logic clk, input logic rst_n,
  input logic [NUM_SOURCES-1:0] source_valid,
  output logic [NUM_SOURCES-1:0] source_ready,
  input logic [NUM_SOURCES*EVENT_WIDTH-1:0] source_event,
  output logic retire_valid, input logic retire_ready,
  output logic [EVENT_WIDTH-1:0] retire_event,
  output logic [SOURCE_WIDTH-1:0] retire_source,
  output logic [AGE_WIDTH-1:0] retire_age
);
  a4_structural_candidate #(
    .NUM_SOURCES(NUM_SOURCES), .EVENT_WIDTH(EVENT_WIDTH),
    .AGE_WIDTH(AGE_WIDTH), .USE_TREE(1'b0)
  ) candidate (.*);
endmodule
