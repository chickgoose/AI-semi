`timescale 1ns/1ps

module a4_quadtree_node #(
  parameter int RADIX        = 4,
  parameter int ADDR_WIDTH   = 16,
  parameter int SOURCE_WIDTH = 4,
  parameter int AGE_WIDTH    = 8,
  parameter int PHASE_WIDTH  = (RADIX <= 1) ? 1 : $clog2(RADIX)
) (
  input  logic                         clk,
  input  logic                         rst_n,
  input  logic [RADIX-1:0]             child_valid,
  output logic [RADIX-1:0]             child_ready,
  input  logic [ADDR_WIDTH-1:0]        child_event [RADIX],
  input  logic [SOURCE_WIDTH-1:0]      child_source [RADIX],
  input  logic [AGE_WIDTH-1:0]         child_age [RADIX],
  output logic                         out_valid,
  input  logic                         out_ready,
  output logic [ADDR_WIDTH-1:0]        out_event,
  output logic [SOURCE_WIDTH-1:0]      out_source,
  output logic [AGE_WIDTH-1:0]         out_age
);
  logic slot_valid;
  logic [ADDR_WIDTH-1:0] slot_event;
  logic [SOURCE_WIDTH-1:0] slot_source;
  logic [AGE_WIDTH-1:0] slot_age;
  logic [PHASE_WIDTH-1:0] rr_phase;
  logic slot_available;
  integer offset;
  integer candidate_child;
  integer selected_child;

  always_comb begin
    out_valid = slot_valid;
    out_event = slot_event;
    out_source = slot_source;
    out_age = slot_age;

    // A full slot can be replaced on the same edge that its old item leaves.
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
      slot_event <= '0;
      slot_source <= '0;
      slot_age <= '0;
      rr_phase <= '0;
    end else if (slot_available) begin
      if (selected_child >= 0) begin
        slot_valid <= 1'b1;
        slot_event <= child_event[selected_child];
        slot_source <= child_source[selected_child];
        if (&child_age[selected_child])
          slot_age <= child_age[selected_child];
        else
          slot_age <= child_age[selected_child] + 1'b1;

        if (selected_child == RADIX-1)
          rr_phase <= '0;
        else
          rr_phase <= PHASE_WIDTH'(selected_child + 1);
      end else begin
        slot_valid <= 1'b0;
        slot_event <= '0;
        slot_source <= '0;
        slot_age <= '0;
      end
    end
  end

`ifndef SYNTHESIS
  initial begin
    if (RADIX < 1)
      $fatal(1, "A4 node RADIX must be positive");
    if ((1 << PHASE_WIDTH) < RADIX)
      $fatal(1, "A4 node PHASE_WIDTH cannot represent RADIX");
  end
`endif
endmodule
