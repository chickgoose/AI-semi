`timescale 1ns/1ps

module a4_moving_block_tree #(
  parameter int NUM_SOURCES  = 16,
  parameter int ADDR_WIDTH   = 32,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES),
  parameter int MAX_ADVANCE  = 2
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
  localparam int TOTAL_NODES = 2 * NUM_SOURCES - 1;
  localparam int FIRST_LEAF  = NUM_SOURCES - 1;
  localparam int MAX_COMB_SKIP = 2;

  logic slot_valid_q [TOTAL_NODES];
  logic slot_valid_d [TOTAL_NODES];
  logic [ADDR_WIDTH-1:0] slot_event_q [TOTAL_NODES];
  logic [ADDR_WIDTH-1:0] slot_event_d [TOTAL_NODES];
  logic [SOURCE_WIDTH-1:0] slot_source_q [TOTAL_NODES];
  logic [SOURCE_WIDTH-1:0] slot_source_d [TOTAL_NODES];
  logic branch_phase_q [FIRST_LEAF];
  logic branch_phase_d [FIRST_LEAF];

  logic [NUM_SOURCES-1:0] accepted;

  always_comb begin
    for (int copy_node = 0; copy_node < TOTAL_NODES; copy_node++) begin
      slot_valid_d[copy_node] = slot_valid_q[copy_node];
      slot_event_d[copy_node] = slot_event_q[copy_node];
      slot_source_d[copy_node] = slot_source_q[copy_node];
    end
    for (int copy_branch = 0; copy_branch < FIRST_LEAF; copy_branch++)
      branch_phase_d[copy_branch] = branch_phase_q[copy_branch];

    retire_valid = 1'b0;
    retire_event = '0;
    retire_source = '0;
    accepted = '0;
    source_ready = '0;

    if (rst_n) begin
      retire_valid = slot_valid_q[0];
      retire_event = slot_event_q[0];
      retire_source = slot_source_q[0];

      // Retirement creates the first downstream clearance. The replacement
      // selected below commits on the same active edge.
      if (slot_valid_q[0] && retire_ready) begin
        slot_valid_d[0] = 1'b0;
        slot_event_d[0] = '0;
        slot_source_d[0] = '0;
      end

      // Each unrolled microstep grants at most one registered edge of travel.
      // MAX_ADVANCE is hard-capped at two to bound combinational skip depth.
      for (int microstep = 0; microstep < MAX_ADVANCE; microstep++) begin
        // A per-cycle accepted bit prevents one held source from being injected
        // again after its leaf clears in the second microstep.
        for (int inject_source = 0; inject_source < NUM_SOURCES; inject_source++) begin
          if (source_valid[inject_source] && !accepted[inject_source] &&
              !slot_valid_d[FIRST_LEAF + inject_source]) begin
            slot_valid_d[FIRST_LEAF + inject_source] = 1'b1;
            slot_event_d[FIRST_LEAF + inject_source] = source_event[inject_source];
            slot_source_d[FIRST_LEAF + inject_source] = SOURCE_WIDTH'(inject_source);
            accepted[inject_source] = 1'b1;
          end
        end

        // Heap order is root to leaves. A destination is examined before its
        // children, so an event cannot traverse two edges in one microstep.
        for (int parent = 0; parent < FIRST_LEAF; parent++) begin
          if (!slot_valid_d[parent]) begin
            if (slot_valid_d[2 * parent + 1] &&
                (!slot_valid_d[2 * parent + 2] || !branch_phase_d[parent])) begin
              slot_valid_d[parent] = 1'b1;
              slot_event_d[parent] = slot_event_d[2 * parent + 1];
              slot_source_d[parent] = slot_source_d[2 * parent + 1];
              slot_valid_d[2 * parent + 1] = 1'b0;
              slot_event_d[2 * parent + 1] = '0;
              slot_source_d[2 * parent + 1] = '0;
              branch_phase_d[parent] = 1'b1;
            end else if (slot_valid_d[2 * parent + 2]) begin
              slot_valid_d[parent] = 1'b1;
              slot_event_d[parent] = slot_event_d[2 * parent + 2];
              slot_source_d[parent] = slot_source_d[2 * parent + 2];
              slot_valid_d[2 * parent + 2] = 1'b0;
              slot_event_d[2 * parent + 2] = '0;
              slot_source_d[2 * parent + 2] = '0;
              branch_phase_d[parent] = 1'b0;
            end
          end
        end
      end
      source_ready = accepted;
    end else begin
      for (int clear_node = 0; clear_node < TOTAL_NODES; clear_node++) begin
        slot_valid_d[clear_node] = 1'b0;
        slot_event_d[clear_node] = '0;
        slot_source_d[clear_node] = '0;
      end
      for (int clear_branch = 0; clear_branch < FIRST_LEAF; clear_branch++)
        branch_phase_d[clear_branch] = 1'b0;
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int reset_node = 0; reset_node < TOTAL_NODES; reset_node++) begin
        slot_valid_q[reset_node] <= 1'b0;
        slot_event_q[reset_node] <= '0;
        slot_source_q[reset_node] <= '0;
      end
      for (int reset_branch = 0; reset_branch < FIRST_LEAF; reset_branch++)
        branch_phase_q[reset_branch] <= 1'b0;
    end else begin
      for (int commit_node = 0; commit_node < TOTAL_NODES; commit_node++) begin
        slot_valid_q[commit_node] <= slot_valid_d[commit_node];
        slot_event_q[commit_node] <= slot_event_d[commit_node];
        slot_source_q[commit_node] <= slot_source_d[commit_node];
      end
      for (int commit_branch = 0; commit_branch < FIRST_LEAF; commit_branch++)
        branch_phase_q[commit_branch] <= branch_phase_d[commit_branch];
    end
  end

`ifndef SYNTHESIS
  logic held_stall;
  logic [ADDR_WIDTH-1:0] held_event;
  logic [SOURCE_WIDTH-1:0] held_source;

  initial begin
    if (NUM_SOURCES < 2 || ((NUM_SOURCES & (NUM_SOURCES - 1)) != 0))
      $fatal(1, "NUM_SOURCES must be a power of two >= 2");
    if (MAX_ADVANCE < 1 || MAX_ADVANCE > MAX_COMB_SKIP)
      $fatal(1, "MAX_ADVANCE exceeds the frozen combinational skip bound");
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      held_stall <= 1'b0;
      held_event <= '0;
      held_source <= '0;
    end else begin
      if (held_stall) begin
        assert (retire_valid)
          else $fatal(1, "retire_valid dropped during a continuous stall");
        assert (retire_event == held_event && retire_source == held_source)
          else $fatal(1, "retire payload changed during a continuous stall");
      end
      held_stall <= retire_valid && !retire_ready;
      if (retire_valid && !retire_ready) begin
        held_event <= retire_event;
        held_source <= retire_source;
      end
      assert ((source_ready & ~source_valid) == '0)
        else $fatal(1, "source accepted without valid");
    end
  end
`endif
endmodule
