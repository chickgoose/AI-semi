`timescale 1ns/1ps

// W4 structural experiment.  STYLE 0 is a normalized transcription of the
// frozen 850fbcf MAX_ADVANCE=2 RTL, STYLE 1 predecodes a shared source
// clearance prefix, and STYLE 2 additionally uses local payload write enables.
module a4_moving_block_w4_core #(
  parameter int NUM_SOURCES  = 16,
  parameter int ADDR_WIDTH   = 32,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES),
  parameter int STYLE        = 0
) (
  input  logic                              clk,
  input  logic                              rst_n,
  input  logic [NUM_SOURCES-1:0]            source_valid,
  output logic [NUM_SOURCES-1:0]            source_ready,
  input  logic [NUM_SOURCES*ADDR_WIDTH-1:0] source_event_flat,
  output logic                              retire_valid,
  input  logic                              retire_ready,
  output logic [ADDR_WIDTH-1:0]             retire_event,
  output logic [SOURCE_WIDTH-1:0]           retire_source
);
  localparam int TOTAL_NODES = 2 * NUM_SOURCES - 1;
  localparam int FIRST_LEAF  = NUM_SOURCES - 1;

  logic slot_valid_q [TOTAL_NODES];
  logic slot_valid_d [TOTAL_NODES];
  logic [ADDR_WIDTH-1:0] slot_event_q [TOTAL_NODES];
  logic [ADDR_WIDTH-1:0] slot_event_d [TOTAL_NODES];
  logic [SOURCE_WIDTH-1:0] slot_source_q [TOTAL_NODES];
  logic [SOURCE_WIDTH-1:0] slot_source_d [TOTAL_NODES];
  logic branch_phase_q [FIRST_LEAF];
  logic branch_phase_d [FIRST_LEAF];
  /* verilator lint_off UNUSEDSIGNAL */
  logic data_write_d [TOTAL_NODES];
  /* verilator lint_on UNUSEDSIGNAL */

  generate
    if (STYLE == 0) begin : g_frozen_normalized
      logic [NUM_SOURCES-1:0] accepted;

      always_comb begin
        for (int copy_node = 0; copy_node < TOTAL_NODES; copy_node++) begin
          slot_valid_d[copy_node] = slot_valid_q[copy_node];
          slot_event_d[copy_node] = slot_event_q[copy_node];
          slot_source_d[copy_node] = slot_source_q[copy_node];
          data_write_d[copy_node] = 1'b1;
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
          if (slot_valid_q[0] && retire_ready) begin
            slot_valid_d[0] = 1'b0;
            slot_event_d[0] = '0;
            slot_source_d[0] = '0;
          end
          for (int microstep = 0; microstep < 2; microstep++) begin
            for (int inject_source = 0; inject_source < NUM_SOURCES; inject_source++) begin
              if (source_valid[inject_source] && !accepted[inject_source] &&
                  !slot_valid_d[FIRST_LEAF + inject_source]) begin
                slot_valid_d[FIRST_LEAF + inject_source] = 1'b1;
                slot_event_d[FIRST_LEAF + inject_source] =
                  source_event_flat[inject_source*ADDR_WIDTH +: ADDR_WIDTH];
                slot_source_d[FIRST_LEAF + inject_source] = SOURCE_WIDTH'(inject_source);
                accepted[inject_source] = 1'b1;
              end
            end
            for (int parent = 0; parent < FIRST_LEAF; parent++) begin
              if (!slot_valid_d[parent]) begin
                if (slot_valid_d[2*parent+1] &&
                    (!slot_valid_d[2*parent+2] || !branch_phase_d[parent])) begin
                  slot_valid_d[parent] = 1'b1;
                  slot_event_d[parent] = slot_event_d[2*parent+1];
                  slot_source_d[parent] = slot_source_d[2*parent+1];
                  slot_valid_d[2*parent+1] = 1'b0;
                  slot_event_d[2*parent+1] = '0;
                  slot_source_d[2*parent+1] = '0;
                  branch_phase_d[parent] = 1'b1;
                end else if (slot_valid_d[2*parent+2]) begin
                  slot_valid_d[parent] = 1'b1;
                  slot_event_d[parent] = slot_event_d[2*parent+2];
                  slot_source_d[parent] = slot_source_d[2*parent+2];
                  slot_valid_d[2*parent+2] = 1'b0;
                  slot_event_d[2*parent+2] = '0;
                  slot_source_d[2*parent+2] = '0;
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
    end else begin : g_shared_clearance
      logic [NUM_SOURCES-1:0] accept_first;
      logic [NUM_SOURCES-1:0] accept_second;
      logic choose_left;
      logic choose_right;

      always_comb begin
        for (int copy_node = 0; copy_node < TOTAL_NODES; copy_node++) begin
          slot_valid_d[copy_node] = slot_valid_q[copy_node];
          slot_event_d[copy_node] = slot_event_q[copy_node];
          slot_source_d[copy_node] = slot_source_q[copy_node];
          data_write_d[copy_node] = 1'b0;
        end
        for (int copy_branch = 0; copy_branch < FIRST_LEAF; copy_branch++)
          branch_phase_d[copy_branch] = branch_phase_q[copy_branch];
        retire_valid = 1'b0;
        retire_event = '0;
        retire_source = '0;
        source_ready = '0;
        accept_first = '0;
        accept_second = '0;
        choose_left = 1'b0;
        choose_right = 1'b0;
        if (rst_n) begin
          retire_valid = slot_valid_q[0];
          retire_event = slot_valid_q[0] ? slot_event_q[0] : '0;
          retire_source = slot_valid_q[0] ? slot_source_q[0] : '0;
          if (slot_valid_q[0] && retire_ready) begin
            slot_valid_d[0] = 1'b0;
            if (STYLE == 1) begin
              slot_event_d[0] = '0;
              slot_source_d[0] = '0;
            end
          end

          // First prefix position: a source owns the clearance already present
          // at its leaf before any merge decision in this cycle.
          for (int inject_first = 0; inject_first < NUM_SOURCES; inject_first++) begin
            accept_first[inject_first] = source_valid[inject_first] &&
              !slot_valid_d[FIRST_LEAF + inject_first];
            if (accept_first[inject_first]) begin
              slot_valid_d[FIRST_LEAF + inject_first] = 1'b1;
              slot_event_d[FIRST_LEAF + inject_first] =
                source_event_flat[inject_first*ADDR_WIDTH +: ADDR_WIDTH];
              slot_source_d[FIRST_LEAF + inject_first] = SOURCE_WIDTH'(inject_first);
              data_write_d[FIRST_LEAF + inject_first] = 1'b1;
            end
          end
          for (int parent_first = 0; parent_first < FIRST_LEAF; parent_first++) begin
            choose_left = !slot_valid_d[parent_first] &&
              slot_valid_d[2*parent_first+1] &&
              (!slot_valid_d[2*parent_first+2] || !branch_phase_d[parent_first]);
            choose_right = !slot_valid_d[parent_first] &&
              slot_valid_d[2*parent_first+2] && !choose_left;
            if (choose_left || choose_right) begin
              if (choose_left) begin
                slot_valid_d[parent_first] = 1'b1;
                slot_event_d[parent_first] = slot_event_d[2*parent_first+1];
                slot_source_d[parent_first] = slot_source_d[2*parent_first+1];
                slot_valid_d[2*parent_first+1] = 1'b0;
                if (STYLE == 1) begin
                  slot_event_d[2*parent_first+1] = '0;
                  slot_source_d[2*parent_first+1] = '0;
                end
                branch_phase_d[parent_first] = 1'b1;
              end else begin
                slot_valid_d[parent_first] = 1'b1;
                slot_event_d[parent_first] = slot_event_d[2*parent_first+2];
                slot_source_d[parent_first] = slot_source_d[2*parent_first+2];
                slot_valid_d[2*parent_first+2] = 1'b0;
                if (STYLE == 1) begin
                  slot_event_d[2*parent_first+2] = '0;
                  slot_source_d[2*parent_first+2] = '0;
                end
                branch_phase_d[parent_first] = 1'b0;
              end
              data_write_d[parent_first] = 1'b1;
            end
          end

          // Second prefix position is available only to a source that was not
          // accepted above and whose occupied leaf advanced in the first pass.
          for (int inject_second = 0; inject_second < NUM_SOURCES; inject_second++) begin
            accept_second[inject_second] = source_valid[inject_second] &&
              !accept_first[inject_second] &&
              !slot_valid_d[FIRST_LEAF + inject_second];
            if (accept_second[inject_second]) begin
              slot_valid_d[FIRST_LEAF + inject_second] = 1'b1;
              slot_event_d[FIRST_LEAF + inject_second] =
                source_event_flat[inject_second*ADDR_WIDTH +: ADDR_WIDTH];
              slot_source_d[FIRST_LEAF + inject_second] = SOURCE_WIDTH'(inject_second);
              data_write_d[FIRST_LEAF + inject_second] = 1'b1;
            end
          end
          for (int parent_second = 0; parent_second < FIRST_LEAF; parent_second++) begin
            choose_left = !slot_valid_d[parent_second] &&
              slot_valid_d[2*parent_second+1] &&
              (!slot_valid_d[2*parent_second+2] || !branch_phase_d[parent_second]);
            choose_right = !slot_valid_d[parent_second] &&
              slot_valid_d[2*parent_second+2] && !choose_left;
            if (choose_left || choose_right) begin
              if (choose_left) begin
                slot_valid_d[parent_second] = 1'b1;
                slot_event_d[parent_second] = slot_event_d[2*parent_second+1];
                slot_source_d[parent_second] = slot_source_d[2*parent_second+1];
                slot_valid_d[2*parent_second+1] = 1'b0;
                if (STYLE == 1) begin
                  slot_event_d[2*parent_second+1] = '0;
                  slot_source_d[2*parent_second+1] = '0;
                end
                branch_phase_d[parent_second] = 1'b1;
              end else begin
                slot_valid_d[parent_second] = 1'b1;
                slot_event_d[parent_second] = slot_event_d[2*parent_second+2];
                slot_source_d[parent_second] = slot_source_d[2*parent_second+2];
                slot_valid_d[2*parent_second+2] = 1'b0;
                if (STYLE == 1) begin
                  slot_event_d[2*parent_second+2] = '0;
                  slot_source_d[2*parent_second+2] = '0;
                end
                branch_phase_d[parent_second] = 1'b0;
              end
              data_write_d[parent_second] = 1'b1;
            end
          end
          source_ready = accept_first | accept_second;
        end else begin
          for (int clear_node = 0; clear_node < TOTAL_NODES; clear_node++) begin
            slot_valid_d[clear_node] = 1'b0;
            slot_event_d[clear_node] = '0;
            slot_source_d[clear_node] = '0;
            data_write_d[clear_node] = 1'b1;
          end
          for (int clear_branch = 0; clear_branch < FIRST_LEAF; clear_branch++)
            branch_phase_d[clear_branch] = 1'b0;
        end
      end
    end
  endgenerate

  generate
    if (STYLE == 2) begin : g_local_enable_registers
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
            if (data_write_d[commit_node]) begin
              slot_event_q[commit_node] <= slot_event_d[commit_node];
              slot_source_q[commit_node] <= slot_source_d[commit_node];
            end
          end
          for (int commit_branch = 0; commit_branch < FIRST_LEAF; commit_branch++)
            branch_phase_q[commit_branch] <= branch_phase_d[commit_branch];
        end
      end
    end else begin : g_baseline_registers
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
    end
  endgenerate

`ifndef SYNTHESIS
  initial begin
    if (NUM_SOURCES < 2 || ((NUM_SOURCES & (NUM_SOURCES - 1)) != 0))
      $fatal(1, "NUM_SOURCES must be a power of two >= 2");
    if (STYLE < 0 || STYLE > 2)
      $fatal(1, "unsupported W4 structural style");
  end
`endif
endmodule

module a4_w4_frozen_normalized #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 32,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (
  input logic clk, input logic rst_n,
  input logic [NUM_SOURCES-1:0] source_valid,
  output logic [NUM_SOURCES-1:0] source_ready,
  input logic [NUM_SOURCES*ADDR_WIDTH-1:0] source_event_flat,
  output logic retire_valid, input logic retire_ready,
  output logic [ADDR_WIDTH-1:0] retire_event,
  output logic [SOURCE_WIDTH-1:0] retire_source
);
  a4_moving_block_w4_core #(.NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH), .STYLE(0)) core (.*);
endmodule

module a4_w4_shared_clearance #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 32,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (
  input logic clk, input logic rst_n,
  input logic [NUM_SOURCES-1:0] source_valid,
  output logic [NUM_SOURCES-1:0] source_ready,
  input logic [NUM_SOURCES*ADDR_WIDTH-1:0] source_event_flat,
  output logic retire_valid, input logic retire_ready,
  output logic [ADDR_WIDTH-1:0] retire_event,
  output logic [SOURCE_WIDTH-1:0] retire_source
);
  a4_moving_block_w4_core #(.NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH), .STYLE(1)) core (.*);
endmodule

module a4_w4_shared_clearance_local_enable #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 32,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (
  input logic clk, input logic rst_n,
  input logic [NUM_SOURCES-1:0] source_valid,
  output logic [NUM_SOURCES-1:0] source_ready,
  input logic [NUM_SOURCES*ADDR_WIDTH-1:0] source_event_flat,
  output logic retire_valid, input logic retire_ready,
  output logic [ADDR_WIDTH-1:0] retire_event,
  output logic [SOURCE_WIDTH-1:0] retire_source
);
  a4_moving_block_w4_core #(.NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH), .STYLE(2)) core (.*);
endmodule
