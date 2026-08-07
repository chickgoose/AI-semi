`timescale 1ns/1ps

module a7_radix4_segmented_event_compactor #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int RETIRE_LANES = 4,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES),
  parameter int COUNT_WIDTH = $clog2(NUM_SOURCES + 1)
) (
  input  logic clk,
  input  logic rst_n,
  input  logic [NUM_SOURCES-1:0] source_valid,
  input  logic [NUM_SOURCES-1:0][ADDR_WIDTH-1:0] source_event,
  output logic [NUM_SOURCES-1:0] source_ready,
  output logic [RETIRE_LANES-1:0] retire_valid,
  output logic [RETIRE_LANES-1:0][ADDR_WIDTH-1:0] retire_event,
  output logic [RETIRE_LANES-1:0][SOURCE_WIDTH-1:0] retire_source,
  input  logic [RETIRE_LANES-1:0] retire_ready
);
  logic [SOURCE_WIDTH-1:0] rotation_base;
  logic [NUM_SOURCES-1:0] source_inflight;
  logic [RETIRE_LANES-1:0] lane_valid;
  logic [RETIRE_LANES-1:0][ADDR_WIDTH-1:0] lane_event;
  logic [RETIRE_LANES-1:0][SOURCE_WIDTH-1:0] lane_source;
  logic [NUM_SOURCES-1:0] retiring_source;
  logic [NUM_SOURCES-1:0] eligible_request;
  logic [NUM_SOURCES-1:0][COUNT_WIDTH-1:0] prefix_count;
  logic [COUNT_WIDTH-1:0] total_count;
  logic [RETIRE_LANES-1:0] lane_available;
  logic [RETIRE_LANES-1:0][COUNT_WIDTH-1:0] available_rank;
  logic [COUNT_WIDTH-1:0] available_count;
  logic [RETIRE_LANES-1:0] selected_valid;
  logic [RETIRE_LANES-1:0][SOURCE_WIDTH-1:0] selected_index;
  logic [NUM_SOURCES-1:0] selected_onehot;
  logic [RETIRE_LANES-1:0] fill_found;
  logic [RETIRE_LANES-1:0][ADDR_WIDTH-1:0] fill_event;
  logic [RETIRE_LANES-1:0][SOURCE_WIDTH-1:0] fill_source;
  logic [NUM_SOURCES-1:0] inflight_next;
  logic [SOURCE_WIDTH-1:0] last_selected_source;
  logic any_selected;
  integer output_lane;
  integer availability_lane;
  integer lane;
  integer slot;
  integer sequential_lane;
  integer running_available;
  integer selected_count;

  a7_radix4_segmented_prefix_count #(
    .NUM_SOURCES(NUM_SOURCES), .COUNT_WIDTH(COUNT_WIDTH)
  ) request_scan (
    .request(eligible_request), .inclusive_count(prefix_count),
    .total_count(total_count)
  );

  a7_shared_rank_index_select #(
    .NUM_SOURCES(NUM_SOURCES), .SELECT_LANES(RETIRE_LANES),
    .SOURCE_WIDTH(SOURCE_WIDTH), .COUNT_WIDTH(COUNT_WIDTH)
  ) index_select (
    .request(eligible_request), .inclusive_count(prefix_count),
    .total_count(total_count), .rotation_base(rotation_base),
    .select_limit(available_count), .selected_valid(selected_valid),
    .selected_index(selected_index), .selected_onehot(selected_onehot)
  );

  always_comb begin
    retire_valid = lane_valid;
    for (output_lane = 0; output_lane < RETIRE_LANES;
         output_lane = output_lane + 1) begin
      retire_event[output_lane] = lane_event[output_lane];
      retire_source[output_lane] = lane_source[output_lane];
    end
    retiring_source = '0;
    for (output_lane = 0; output_lane < RETIRE_LANES;
         output_lane = output_lane + 1)
      if (lane_valid[output_lane] && retire_ready[output_lane])
        retiring_source[lane_source[output_lane]] = 1'b1;
  end

  always_comb eligible_request =
    source_valid & (~source_inflight | retiring_source);

  always_comb begin
    running_available = 0;
    lane_available = '0;
    for (availability_lane = 0; availability_lane < RETIRE_LANES;
         availability_lane = availability_lane + 1) begin
      lane_available[availability_lane] =
        !lane_valid[availability_lane] || retire_ready[availability_lane];
      available_rank[availability_lane] = COUNT_WIDTH'(running_available);
      if (lane_available[availability_lane])
        running_available = running_available + 1;
    end
    available_count = COUNT_WIDTH'(running_available);
  end

  always_comb begin
    lane = 0;
    slot = 0;
    source_ready = selected_onehot;
    selected_count = 0;
    for (slot = 0; slot < RETIRE_LANES; slot = slot + 1)
      if (selected_valid[slot]) selected_count = selected_count + 1;

    fill_found = '0;
    fill_event = '0;
    fill_source = '0;
    for (lane = 0; lane < RETIRE_LANES; lane = lane + 1) begin
      if (lane_available[lane]) begin
        for (slot = 0; slot < RETIRE_LANES; slot = slot + 1) begin
          if ((available_rank[lane] == COUNT_WIDTH'(slot)) &&
              selected_valid[slot]) begin
            fill_found[lane] = 1'b1;
            fill_source[lane] = selected_index[slot];
            fill_event[lane] = source_event[selected_index[slot]];
          end
        end
      end
    end

    any_selected = (selected_count != 0);
    last_selected_source = rotation_base;
    for (slot = 0; slot < RETIRE_LANES; slot = slot + 1)
      if (selected_valid[slot] && (slot == selected_count-1))
        last_selected_source = selected_index[slot];
    inflight_next = (source_inflight & ~retiring_source) | source_ready;
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rotation_base <= '0;
      source_inflight <= '0;
      lane_valid <= '0;
      lane_event <= '0;
      lane_source <= '0;
    end else begin
      source_inflight <= inflight_next;
      if (any_selected) begin
        if (last_selected_source == SOURCE_WIDTH'(NUM_SOURCES-1))
          rotation_base <= '0;
        else
          rotation_base <= last_selected_source + SOURCE_WIDTH'(1);
      end
      for (sequential_lane = 0; sequential_lane < RETIRE_LANES;
           sequential_lane = sequential_lane + 1) begin
        if (lane_available[sequential_lane]) begin
          lane_valid[sequential_lane] <= fill_found[sequential_lane];
          if (fill_found[sequential_lane]) begin
            lane_event[sequential_lane] <= fill_event[sequential_lane];
            lane_source[sequential_lane] <= fill_source[sequential_lane];
          end else begin
            lane_event[sequential_lane] <= '0;
            lane_source[sequential_lane] <= '0;
          end
        end
      end
    end
  end

  initial begin
    if ((RETIRE_LANES != 1) && (RETIRE_LANES != 2) &&
        (RETIRE_LANES != 4) && (RETIRE_LANES != 8))
      $fatal(1, "A7 segmented supports RETIRE_LANES=1,2,4,8");
    if (RETIRE_LANES > NUM_SOURCES)
      $fatal(1, "A7 segmented RETIRE_LANES must not exceed NUM_SOURCES");
  end
endmodule
