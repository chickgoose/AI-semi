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
  input  logic [ADDR_WIDTH-1:0]    source_event_i [NUM_SOURCES],
  output logic [RETIRE_LANES-1:0]  retire_valid_o,
  input  logic [RETIRE_LANES-1:0]  retire_ready_i,
  output logic [ADDR_WIDTH-1:0]    retire_event_o [RETIRE_LANES],
  output logic [SOURCE_WIDTH-1:0]  retire_source_o [RETIRE_LANES]
);
  logic link_valid [RETIRE_LANES][STRIPE_DEPTH+1];
  logic link_ready [RETIRE_LANES][STRIPE_DEPTH+1];
  logic [ADDR_WIDTH-1:0] link_event [RETIRE_LANES][STRIPE_DEPTH+1];
  logic [SOURCE_WIDTH-1:0] link_source [RETIRE_LANES][STRIPE_DEPTH+1];

  logic cell_local_valid [RETIRE_LANES][STRIPE_DEPTH];
  logic cell_local_ready [RETIRE_LANES][STRIPE_DEPTH];
  logic [ADDR_WIDTH-1:0] cell_local_event [RETIRE_LANES][STRIPE_DEPTH];
  logic [SOURCE_WIDTH-1:0] cell_local_source [RETIRE_LANES][STRIPE_DEPTH];

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
          .downstream_source_o(link_source[lane][position+1])
        );
      end
    end
  endgenerate
endmodule
