`timescale 1ns/1ps

module a9_distributed_token_fabric #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int RETIRE_LANES = 4,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES),
  parameter int STRIPE_DEPTH = NUM_SOURCES / RETIRE_LANES
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
  logic link_valid [RETIRE_LANES][STRIPE_DEPTH+1];
  logic link_ready [RETIRE_LANES][STRIPE_DEPTH+1];
  logic [ADDR_WIDTH-1:0] link_event [RETIRE_LANES][STRIPE_DEPTH+1];
  logic [SOURCE_WIDTH-1:0] link_source [RETIRE_LANES][STRIPE_DEPTH+1];

  logic cell_local_valid [RETIRE_LANES][STRIPE_DEPTH];
  logic cell_local_ready [RETIRE_LANES][STRIPE_DEPTH];
  logic [ADDR_WIDTH-1:0] cell_local_event [RETIRE_LANES][STRIPE_DEPTH];
  logic [SOURCE_WIDTH-1:0] cell_local_source [RETIRE_LANES][STRIPE_DEPTH];
  logic [1:0] cell_transport_occupancy [RETIRE_LANES][STRIPE_DEPTH];

  initial begin
    if (RETIRE_LANES <= 0)
      $fatal(1, "A9_FABRIC RETIRE_LANES must be positive");
    if ((NUM_SOURCES % RETIRE_LANES) != 0)
      $fatal(1, "A9_FABRIC requires equal fixed stripes sources=%0d lanes=%0d",
             NUM_SOURCES, RETIRE_LANES);
  end

  genvar lane;
  generate
    for (lane = 0; lane < RETIRE_LANES; lane = lane + 1) begin : stripe
      assign link_valid[lane][0] = 1'b0;
      assign link_event[lane][0] = '0;
      assign link_source[lane][0] = '0;
      assign link_ready[lane][STRIPE_DEPTH] = retire_ready_i[lane];

      assign retire_valid_o[lane] = link_valid[lane][STRIPE_DEPTH];
      assign retire_event_o[lane] = link_event[lane][STRIPE_DEPTH];
      assign retire_source_o[lane] = link_source[lane][STRIPE_DEPTH];

      genvar position;
      for (position = 0; position < STRIPE_DEPTH;
           position = position + 1) begin : position_cell
        localparam int SOURCE_INDEX =
          (lane % 2 == 0) ?
            (lane * STRIPE_DEPTH + position) :
            (lane * STRIPE_DEPTH + (STRIPE_DEPTH - 1 - position));

        assign cell_local_valid[lane][position] = source_valid_i[SOURCE_INDEX];
        assign cell_local_event[lane][position] = source_event_i[SOURCE_INDEX];
        assign cell_local_source[lane][position] = SOURCE_WIDTH'(SOURCE_INDEX);
        assign source_ready_o[SOURCE_INDEX] = cell_local_ready[lane][position];

        a9_empty_slot_cell #(
          .ADDR_WIDTH(ADDR_WIDTH),
          .SOURCE_WIDTH(SOURCE_WIDTH)
        ) u_cell (
          .clk_i(clk_i),
          .rst_ni(rst_ni),
          .local_valid_i(cell_local_valid[lane][position]),
          .local_ready_o(cell_local_ready[lane][position]),
          .local_event_i(cell_local_event[lane][position]),
          .local_source_i(cell_local_source[lane][position]),
          .upstream_valid_i(link_valid[lane][position]),
          .upstream_ready_o(link_ready[lane][position]),
          .upstream_event_i(link_event[lane][position]),
          .upstream_source_i(link_source[lane][position]),
          .downstream_valid_o(link_valid[lane][position+1]),
          .downstream_ready_i(link_ready[lane][position+1]),
          .downstream_event_o(link_event[lane][position+1]),
          .downstream_source_o(link_source[lane][position+1]),
          .transport_occupancy_o(
            cell_transport_occupancy[lane][position])
        );
      end
    end
  endgenerate

`ifndef SYNTHESIS
  integer debug_cycle_occupancy;
  integer debug_retire_transfers_cycle;
  integer debug_lane_index;
  integer debug_position_index;
  integer debug_cycles_q;
  integer debug_occupied_slot_cycles_q;
  integer debug_retire_transfers_q;

  always_comb begin
    debug_cycle_occupancy = 0;
    debug_retire_transfers_cycle = 0;
    for (debug_lane_index = 0; debug_lane_index < RETIRE_LANES;
         debug_lane_index = debug_lane_index + 1) begin
      if (retire_valid_o[debug_lane_index] &&
          retire_ready_i[debug_lane_index])
        debug_retire_transfers_cycle = debug_retire_transfers_cycle + 1;
      for (debug_position_index = 0; debug_position_index < STRIPE_DEPTH;
           debug_position_index = debug_position_index + 1)
        debug_cycle_occupancy = debug_cycle_occupancy +
          cell_transport_occupancy[debug_lane_index][debug_position_index];
    end
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      debug_cycles_q <= 0;
      debug_occupied_slot_cycles_q <= 0;
      debug_retire_transfers_q <= 0;
    end else begin
      debug_cycles_q <= debug_cycles_q + 1;
      debug_occupied_slot_cycles_q <=
        debug_occupied_slot_cycles_q + debug_cycle_occupancy;
      debug_retire_transfers_q <=
        debug_retire_transfers_q + debug_retire_transfers_cycle;
    end
  end

  final begin
    $display("A9_TOKEN_METRICS cycles=%0d occupied_slot_cycles=%0d slot_capacity_cycles=%0d token_occupancy_util=%0.6f retire_transfers=%0d lane_service_util=%0.6f empty_slot_rtt_bound=%0d",
      debug_cycles_q, debug_occupied_slot_cycles_q,
      debug_cycles_q * 2 * NUM_SOURCES,
      (debug_cycles_q == 0) ? 0.0 :
        real'(debug_occupied_slot_cycles_q) /
        real'(debug_cycles_q * 2 * NUM_SOURCES),
      debug_retire_transfers_q,
      (debug_cycles_q == 0) ? 0.0 :
        real'(debug_retire_transfers_q) /
        real'(debug_cycles_q * RETIRE_LANES),
      STRIPE_DEPTH);
  end
`endif
endmodule
