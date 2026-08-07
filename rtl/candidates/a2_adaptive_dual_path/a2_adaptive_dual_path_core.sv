// A2 architecture skeleton. The functional implementation is added in the
// second-stage commit after the ordering and mode invariants are frozen in the
// research document. This unit is intentionally not present in any runner or
// shared file list yet.
module a2_adaptive_dual_path_core #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int RESERVOIR_DEPTH = 8,
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
  // Functional sparse bypass, banked storage, global ordering pointers, and
  // level+slope+hysteresis control are introduced in the next commit.
  always_comb begin
    source_ready_o = '0;
    retire_valid_o = 1'b0;
    retire_event_o = '0;
    retire_source_o = '0;
  end
endmodule
