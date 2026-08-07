`timescale 1ns/1ps

module a7_parallel_event_compactor #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int RETIRE_LANES = 4,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES),
  parameter int COUNT_WIDTH = $clog2(NUM_SOURCES + 1)
) (
  input  logic clk,
  input  logic rst_n,
  input  logic [NUM_SOURCES-1:0] source_valid,
  input  logic [ADDR_WIDTH-1:0] source_event [NUM_SOURCES],
  output logic [NUM_SOURCES-1:0] source_ready,
  output logic [RETIRE_LANES-1:0] retire_valid,
  output logic [ADDR_WIDTH-1:0] retire_event [RETIRE_LANES],
  output logic [SOURCE_WIDTH-1:0] retire_source [RETIRE_LANES],
  input  logic [RETIRE_LANES-1:0] retire_ready
);
  logic [SOURCE_WIDTH-1:0] rotation_base;
  logic [NUM_SOURCES-1:0] source_inflight;
  logic [RETIRE_LANES-1:0] lane_valid;
  logic [ADDR_WIDTH-1:0] lane_event [RETIRE_LANES];
  logic [SOURCE_WIDTH-1:0] lane_source [RETIRE_LANES];

  logic [NUM_SOURCES-1:0] retiring_source;
  logic [NUM_SOURCES-1:0] eligible_request;
  logic [COUNT_WIDTH-1:0] prefix_count [NUM_SOURCES];
  logic [COUNT_WIDTH-1:0] total_count;
  logic [COUNT_WIDTH-1:0] cyclic_rank [NUM_SOURCES];
  logic [COUNT_WIDTH-1:0] base_before;
  logic [COUNT_WIDTH-1:0] available_count;
  logic [COUNT_WIDTH-1:0] available_rank [RETIRE_LANES];
  logic [RETIRE_LANES-1:0] lane_available;
  logic [RETIRE_LANES-1:0] fill_found;
  logic [ADDR_WIDTH-1:0] fill_event [RETIRE_LANES];
  logic [SOURCE_WIDTH-1:0] fill_source [RETIRE_LANES];
  logic [NUM_SOURCES-1:0] inflight_next;
  logic [SOURCE_WIDTH-1:0] last_selected_source;
  logic any_selected;

  integer source;
  integer lane;
  integer lane_output;
  integer lane_seq;
  integer running_available;
  integer selected_count;

  a7_parallel_prefix_count #(
    .NUM_SOURCES(NUM_SOURCES),
    .COUNT_WIDTH(COUNT_WIDTH)
  ) request_scan (
    .request(eligible_request),
    .inclusive_count(prefix_count),
    .total_count(total_count)
  );

  always_comb begin
    retire_valid = lane_valid;
    for (lane_output = 0; lane_output < RETIRE_LANES;
         lane_output = lane_output + 1) begin
      retire_event[lane_output] = lane_event[lane_output];
      retire_source[lane_output] = lane_source[lane_output];
    end

    retiring_source = '0;
    for (lane_output = 0; lane_output < RETIRE_LANES;
         lane_output = lane_output + 1)
      if (lane_valid[lane_output] && retire_ready[lane_output])
        retiring_source[lane_source[lane_output]] = 1'b1;
  end

  // A source with a stalled older event cannot be admitted into another lane.
  // Same-cycle retire/refill remains legal and preserves source-local order.
  always_comb eligible_request =
    source_valid & (~source_inflight | retiring_source);

  always_comb begin
    running_available = 0;
    lane_available = '0;
    for (lane = 0; lane < RETIRE_LANES; lane = lane + 1) begin
      lane_available[lane] = !lane_valid[lane] || retire_ready[lane];
      available_rank[lane] = COUNT_WIDTH'(running_available);
      if (lane_available[lane])
        running_available = running_available + 1;
    end
    available_count = COUNT_WIDTH'(running_available);

    if (rotation_base == '0)
      base_before = '0;
    else
      base_before = prefix_count[rotation_base-1];

    for (source = 0; source < NUM_SOURCES; source = source + 1) begin
      if (source >= rotation_base)
        cyclic_rank[source] =
          (prefix_count[source] - COUNT_WIDTH'(eligible_request[source])) -
          base_before;
      else
        cyclic_rank[source] =
          (total_count - base_before) +
          (prefix_count[source] - COUNT_WIDTH'(eligible_request[source]));
    end

    source_ready = '0;
    selected_count = 0;
    for (source = 0; source < NUM_SOURCES; source = source + 1) begin
      if (eligible_request[source] &&
          (cyclic_rank[source] < available_count)) begin
        source_ready[source] = 1'b1;
        selected_count = selected_count + 1;
      end
    end

    fill_found = '0;
    for (lane = 0; lane < RETIRE_LANES; lane = lane + 1) begin
      fill_event[lane] = '0;
      fill_source[lane] = '0;
      if (lane_available[lane]) begin
        for (source = 0; source < NUM_SOURCES; source = source + 1) begin
          if (!fill_found[lane] && source_ready[source] &&
              (cyclic_rank[source] == available_rank[lane])) begin
            fill_found[lane] = 1'b1;
            fill_event[lane] = source_event[source];
            fill_source[lane] = SOURCE_WIDTH'(source);
          end
        end
      end
    end

    any_selected = (selected_count != 0);
    last_selected_source = rotation_base;
    for (source = 0; source < NUM_SOURCES; source = source + 1)
      if (source_ready[source] &&
          (cyclic_rank[source] == COUNT_WIDTH'(selected_count-1)))
        last_selected_source = SOURCE_WIDTH'(source);

    inflight_next = source_inflight & ~retiring_source;
    inflight_next = inflight_next | source_ready;
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rotation_base <= '0;
      source_inflight <= '0;
      lane_valid <= '0;
      for (lane_seq = 0; lane_seq < RETIRE_LANES; lane_seq = lane_seq + 1) begin
        lane_event[lane_seq] <= '0;
        lane_source[lane_seq] <= '0;
      end
    end else begin
      source_inflight <= inflight_next;
      if (any_selected) begin
        if (last_selected_source == SOURCE_WIDTH'(NUM_SOURCES-1))
          rotation_base <= '0;
        else
          rotation_base <= last_selected_source + SOURCE_WIDTH'(1);
      end

      for (lane_seq = 0; lane_seq < RETIRE_LANES; lane_seq = lane_seq + 1) begin
        if (lane_available[lane_seq]) begin
          lane_valid[lane_seq] <= fill_found[lane_seq];
          if (fill_found[lane_seq]) begin
            lane_event[lane_seq] <= fill_event[lane_seq];
            lane_source[lane_seq] <= fill_source[lane_seq];
          end else begin
            lane_event[lane_seq] <= '0;
            lane_source[lane_seq] <= '0;
          end
        end
      end
    end
  end

  initial begin
    if ((RETIRE_LANES != 1) && (RETIRE_LANES != 2) && (RETIRE_LANES != 4))
      $fatal(1, "A7 supports RETIRE_LANES=1,2,4");
    if (RETIRE_LANES > NUM_SOURCES)
      $fatal(1, "A7 RETIRE_LANES must not exceed NUM_SOURCES");
  end
endmodule
