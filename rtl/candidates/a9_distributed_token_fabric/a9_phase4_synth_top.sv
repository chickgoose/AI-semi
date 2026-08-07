`timescale 1ns/1ps

// Structural-comparison shell only.  Every candidate sees the same registered
// ingress and is observed through the same registered egress boundary.
module a9_phase4_synth_top #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int RETIRE_LANES = 4,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (
  input  logic                     clk_i,
  input  logic                     rst_ni,
  input  logic [NUM_SOURCES-1:0]   source_valid_i,
  output logic [NUM_SOURCES-1:0]   source_ready_o,
`ifdef A9_YOSYS
  input  logic [NUM_SOURCES-1:0][ADDR_WIDTH-1:0] source_event_i,
`else
  input  logic [ADDR_WIDTH-1:0]    source_event_i [NUM_SOURCES],
`endif
  output logic [RETIRE_LANES-1:0]  retire_valid_o,
  input  logic [RETIRE_LANES-1:0]  retire_ready_i,
`ifdef A9_YOSYS
  output logic [RETIRE_LANES-1:0][ADDR_WIDTH-1:0] retire_event_o,
  output logic [RETIRE_LANES-1:0][SOURCE_WIDTH-1:0] retire_source_o
`else
  output logic [ADDR_WIDTH-1:0]    retire_event_o [RETIRE_LANES],
  output logic [SOURCE_WIDTH-1:0]  retire_source_o [RETIRE_LANES]
`endif
);
  logic [NUM_SOURCES-1:0] source_valid_boundary_q;
`ifdef A9_YOSYS
  logic [NUM_SOURCES-1:0][ADDR_WIDTH-1:0] source_event_boundary_q;
`else
  logic [ADDR_WIDTH-1:0] source_event_boundary_q [NUM_SOURCES];
`endif
  logic [RETIRE_LANES-1:0] retire_ready_boundary_q;

  logic [NUM_SOURCES-1:0] source_ready_boundary_d;
  logic [RETIRE_LANES-1:0] retire_valid_boundary_d;
`ifdef A9_YOSYS
  logic [RETIRE_LANES-1:0][ADDR_WIDTH-1:0] retire_event_boundary_d;
  logic [RETIRE_LANES-1:0][SOURCE_WIDTH-1:0] retire_source_boundary_d;
`else
  logic [ADDR_WIDTH-1:0] retire_event_boundary_d [RETIRE_LANES];
  logic [SOURCE_WIDTH-1:0] retire_source_boundary_d [RETIRE_LANES];
`endif

`ifdef A9_PHASE4_CENTRAL
  a9_centralized_reference #(
`elsif A9_PHASE4_DIFFUSIVE
  a9_neighbor_handoff_fabric #(
`else
  a9_distributed_token_fabric #(
`endif
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(RETIRE_LANES),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) u_candidate (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .source_valid_i(source_valid_boundary_q),
    .source_ready_o(source_ready_boundary_d),
    .source_event_i(source_event_boundary_q),
    .retire_valid_o(retire_valid_boundary_d),
    .retire_ready_i(retire_ready_boundary_q),
    .retire_event_o(retire_event_boundary_d),
    .retire_source_o(retire_source_boundary_d)
  );

  integer source_index;
  integer lane_index;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      source_valid_boundary_q <= '0;
      retire_ready_boundary_q <= '0;
      source_ready_o <= '0;
      retire_valid_o <= '0;
      for (source_index = 0; source_index < NUM_SOURCES;
           source_index = source_index + 1)
        source_event_boundary_q[source_index] <= '0;
      for (lane_index = 0; lane_index < RETIRE_LANES;
           lane_index = lane_index + 1) begin
        retire_event_o[lane_index] <= '0;
        retire_source_o[lane_index] <= '0;
      end
    end else begin
      source_valid_boundary_q <= source_valid_i;
      retire_ready_boundary_q <= retire_ready_i;
      source_ready_o <= source_ready_boundary_d;
      retire_valid_o <= retire_valid_boundary_d;
      for (source_index = 0; source_index < NUM_SOURCES;
           source_index = source_index + 1)
        source_event_boundary_q[source_index] <= source_event_i[source_index];
      for (lane_index = 0; lane_index < RETIRE_LANES;
           lane_index = lane_index + 1) begin
        retire_event_o[lane_index] <= retire_event_boundary_d[lane_index];
        retire_source_o[lane_index] <= retire_source_boundary_d[lane_index];
      end
    end
  end
endmodule
