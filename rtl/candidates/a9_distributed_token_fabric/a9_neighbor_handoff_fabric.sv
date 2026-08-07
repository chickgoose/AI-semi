`timescale 1ns/1ps

module a9_neighbor_handoff_fabric #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int RETIRE_LANES = 4,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
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
  logic [RETIRE_LANES-1:0] base_valid;
  logic [RETIRE_LANES-1:0] base_ready;
  logic [ADDR_WIDTH-1:0] base_event [RETIRE_LANES];
  logic [SOURCE_WIDTH-1:0] base_source [RETIRE_LANES];
  logic [RETIRE_LANES-1:0] migrate;
  logic [RETIRE_LANES-1:0] pinned_q;

  initial begin
    if ((RETIRE_LANES < 2) || ((RETIRE_LANES % 2) != 0))
      $fatal(1, "A9_HANDOFF requires an even RETIRE_LANES >= 2");
  end

  a9_distributed_token_fabric #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(RETIRE_LANES),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) u_static_fabric (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .source_valid_i(source_valid_i),
    .source_ready_o(source_ready_o),
    .source_event_i(source_event_i),
    .retire_valid_o(base_valid),
    .retire_ready_i(base_ready),
    .retire_event_o(base_event),
    .retire_source_o(base_source)
  );

  genvar lane;
  generate
    for (lane = 0; lane < RETIRE_LANES; lane = lane + 1) begin : endpoint
      localparam int NEIGHBOR = lane ^ 1;

      // H2: only a fresh, never-stalled head may use an empty ready neighbor.
      // The migrant retires directly on that edge and is never stored/copied.
      assign migrate[lane] = base_valid[lane] && !pinned_q[lane] &&
                             !retire_ready_i[lane] &&
                             retire_ready_i[NEIGHBOR] &&
                             !base_valid[NEIGHBOR];
      assign base_ready[lane] = retire_ready_i[lane] || migrate[lane];

      assign retire_valid_o[lane] = migrate[NEIGHBOR] ||
                                    (base_valid[lane] && !migrate[lane]);
      assign retire_event_o[lane] = migrate[NEIGHBOR] ?
                                    base_event[NEIGHBOR] : base_event[lane];
      assign retire_source_o[lane] = migrate[NEIGHBOR] ?
                                     base_source[NEIGHBOR] : base_source[lane];

      always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni)
          pinned_q[lane] <= 1'b0;
        else if (!base_valid[lane] || base_ready[lane])
          pinned_q[lane] <= 1'b0;
        else
          pinned_q[lane] <= 1'b1;
      end
    end
  endgenerate

`ifndef SYNTHESIS
  integer debug_migrations_cycle;
  integer debug_pin_blocks_cycle;
  integer debug_comb_lane_index;
  integer debug_seq_lane_index;
  integer debug_cycles_q;
  integer debug_migrations_q;
  integer debug_pin_blocks_q;
  logic [RETIRE_LANES-1:0] debug_stalled_q;
  logic [ADDR_WIDTH-1:0] debug_stalled_event_q [RETIRE_LANES];
  logic [SOURCE_WIDTH-1:0] debug_stalled_source_q [RETIRE_LANES];

  always_comb begin
    debug_migrations_cycle = 0;
    debug_pin_blocks_cycle = 0;
    for (debug_comb_lane_index = 0; debug_comb_lane_index < RETIRE_LANES;
         debug_comb_lane_index = debug_comb_lane_index + 1) begin
      if (migrate[debug_comb_lane_index])
        debug_migrations_cycle = debug_migrations_cycle + 1;
      if (base_valid[debug_comb_lane_index] &&
          pinned_q[debug_comb_lane_index] &&
          !retire_ready_i[debug_comb_lane_index] &&
          retire_ready_i[debug_comb_lane_index ^ 1] &&
          !base_valid[debug_comb_lane_index ^ 1])
        debug_pin_blocks_cycle = debug_pin_blocks_cycle + 1;
    end
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      debug_cycles_q <= 0;
      debug_migrations_q <= 0;
      debug_pin_blocks_q <= 0;
      debug_stalled_q <= '0;
      for (debug_seq_lane_index = 0; debug_seq_lane_index < RETIRE_LANES;
           debug_seq_lane_index = debug_seq_lane_index + 1) begin
        debug_stalled_event_q[debug_seq_lane_index] <= '0;
        debug_stalled_source_q[debug_seq_lane_index] <= '0;
      end
    end else begin
      debug_cycles_q <= debug_cycles_q + 1;
      debug_migrations_q <= debug_migrations_q + debug_migrations_cycle;
      debug_pin_blocks_q <= debug_pin_blocks_q + debug_pin_blocks_cycle;
      for (debug_seq_lane_index = 0; debug_seq_lane_index < RETIRE_LANES;
           debug_seq_lane_index = debug_seq_lane_index + 1) begin
        if (debug_stalled_q[debug_seq_lane_index] &&
            retire_valid_o[debug_seq_lane_index] &&
            !retire_ready_i[debug_seq_lane_index]) begin
          assert (retire_event_o[debug_seq_lane_index] ==
                  debug_stalled_event_q[debug_seq_lane_index]);
          assert (retire_source_o[debug_seq_lane_index] ==
                  debug_stalled_source_q[debug_seq_lane_index]);
        end
        debug_stalled_q[debug_seq_lane_index] <=
          retire_valid_o[debug_seq_lane_index] &&
          !retire_ready_i[debug_seq_lane_index];
        if (retire_valid_o[debug_seq_lane_index] &&
            !retire_ready_i[debug_seq_lane_index]) begin
          debug_stalled_event_q[debug_seq_lane_index] <=
            retire_event_o[debug_seq_lane_index];
          debug_stalled_source_q[debug_seq_lane_index] <=
            retire_source_o[debug_seq_lane_index];
        end
        assert (!(migrate[debug_seq_lane_index] &&
                  pinned_q[debug_seq_lane_index]));
        if (migrate[debug_seq_lane_index]) begin
          assert (!base_valid[debug_seq_lane_index ^ 1]);
          assert (retire_ready_i[debug_seq_lane_index ^ 1]);
        end
      end
    end
  end

  final begin
    $display("A9_DIFFUSION_METRICS cycles=%0d migrations=%0d pin_block_cycles=%0d pin_bits=%0d toggle_bits=0 migration_latency_cycles=0",
      debug_cycles_q, debug_migrations_q, debug_pin_blocks_q, RETIRE_LANES);
  end
`endif
endmodule
