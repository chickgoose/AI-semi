`timescale 1ns/1ps

// Measurement-only centralized reference for A9.  It intentionally uses a
// flat per-stripe request scan and is never a fallback inside the A9 fabric.
module a9_centralized_reference #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int RETIRE_LANES = 4,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES),
  parameter int STRIPE_DEPTH = NUM_SOURCES / RETIRE_LANES,
  parameter int STRIPE_INDEX_WIDTH =
    (STRIPE_DEPTH <= 1) ? 1 : $clog2(STRIPE_DEPTH)
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
  logic [NUM_SOURCES-1:0] ingress_valid_q;
  logic [ADDR_WIDTH-1:0] ingress_event_q [NUM_SOURCES];
  logic [STRIPE_INDEX_WIDTH-1:0] rr_start_q [RETIRE_LANES];
  logic output_valid_q [RETIRE_LANES];
  logic [ADDR_WIDTH-1:0] output_event_q [RETIRE_LANES];
  logic [SOURCE_WIDTH-1:0] output_source_q [RETIRE_LANES];
  logic output_available [RETIRE_LANES];
  integer selected_source [RETIRE_LANES];
  integer comb_lane_index;
  integer comb_offset;
  integer candidate_position;
  integer candidate_source;
  integer seq_lane_index;
  integer seq_source_index;

  initial begin
    if ((NUM_SOURCES % RETIRE_LANES) != 0)
      $fatal(1, "A9_CENTRAL_REFERENCE requires equal stripes");
  end

  always_comb begin
    source_ready_o = ~ingress_valid_q;
    candidate_position = 0;
    candidate_source = 0;
    for (comb_lane_index = 0; comb_lane_index < RETIRE_LANES;
         comb_lane_index = comb_lane_index + 1) begin
      output_available[comb_lane_index] =
        !output_valid_q[comb_lane_index] || retire_ready_i[comb_lane_index];
      selected_source[comb_lane_index] = -1;
      if (output_available[comb_lane_index]) begin
        for (comb_offset = 0; comb_offset < STRIPE_DEPTH;
             comb_offset = comb_offset + 1) begin
          candidate_position = rr_start_q[comb_lane_index] + comb_offset;
          if (candidate_position >= STRIPE_DEPTH)
            candidate_position = candidate_position - STRIPE_DEPTH;
          candidate_source =
            comb_lane_index * STRIPE_DEPTH + candidate_position;
          if ((selected_source[comb_lane_index] < 0) &&
              ingress_valid_q[candidate_source])
            selected_source[comb_lane_index] = candidate_source;
        end
      end
      if (selected_source[comb_lane_index] >= 0)
        source_ready_o[selected_source[comb_lane_index]] = 1'b1;

      retire_valid_o[comb_lane_index] = output_valid_q[comb_lane_index];
      retire_event_o[comb_lane_index] = output_event_q[comb_lane_index];
      retire_source_o[comb_lane_index] = output_source_q[comb_lane_index];
    end
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      ingress_valid_q <= '0;
      for (seq_source_index = 0; seq_source_index < NUM_SOURCES;
           seq_source_index = seq_source_index + 1)
        ingress_event_q[seq_source_index] <= '0;
      for (seq_lane_index = 0; seq_lane_index < RETIRE_LANES;
           seq_lane_index = seq_lane_index + 1) begin
        rr_start_q[seq_lane_index] <= '0;
        output_valid_q[seq_lane_index] <= 1'b0;
        output_event_q[seq_lane_index] <= '0;
        output_source_q[seq_lane_index] <= '0;
      end
    end else begin
      for (seq_source_index = 0; seq_source_index < NUM_SOURCES;
           seq_source_index = seq_source_index + 1) begin
        if (source_valid_i[seq_source_index] &&
            source_ready_o[seq_source_index]) begin
          ingress_valid_q[seq_source_index] <= 1'b1;
          ingress_event_q[seq_source_index] <= source_event_i[seq_source_index];
        end else begin
          for (seq_lane_index = 0; seq_lane_index < RETIRE_LANES;
               seq_lane_index = seq_lane_index + 1) begin
            if (selected_source[seq_lane_index] == seq_source_index)
              ingress_valid_q[seq_source_index] <= 1'b0;
          end
        end
      end

      for (seq_lane_index = 0; seq_lane_index < RETIRE_LANES;
           seq_lane_index = seq_lane_index + 1) begin
        if (output_available[seq_lane_index]) begin
          if (selected_source[seq_lane_index] >= 0) begin
            output_valid_q[seq_lane_index] <= 1'b1;
            output_event_q[seq_lane_index] <=
              ingress_event_q[selected_source[seq_lane_index]];
            output_source_q[seq_lane_index] <=
              SOURCE_WIDTH'(selected_source[seq_lane_index]);
            if ((selected_source[seq_lane_index] -
                 seq_lane_index * STRIPE_DEPTH) == STRIPE_DEPTH-1)
              rr_start_q[seq_lane_index] <= '0;
            else
              rr_start_q[seq_lane_index] <= STRIPE_INDEX_WIDTH'(
                selected_source[seq_lane_index] -
                seq_lane_index * STRIPE_DEPTH + 1);
          end else begin
            output_valid_q[seq_lane_index] <= 1'b0;
          end
        end
      end
    end
  end
endmodule
