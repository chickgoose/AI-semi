`timescale 1ns/1ps

// One conventional rotation-aware priority selector. The K-lane reference
// instantiates K copies and masks each earlier winner from the next copy.
module a7_rotating_priority_selector #(
  parameter int NUM_SOURCES = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (
  input  logic [NUM_SOURCES-1:0] request,
  input  logic [SOURCE_WIDTH-1:0] rotation_base,
  output logic selected_valid,
  output logic [SOURCE_WIDTH-1:0] selected_source,
  output logic [NUM_SOURCES-1:0] selected_onehot
);
  logic [2*NUM_SOURCES-1:0] doubled_request;
  logic [2*NUM_SOURCES-1:0] shifted_request;
  logic [NUM_SOURCES-1:0] rotated_request;
  integer offset;
  always_comb begin
    doubled_request = {request, request};
    shifted_request = doubled_request >> rotation_base;
    rotated_request = shifted_request[NUM_SOURCES-1:0];
    selected_valid = 1'b0;
    selected_source = '0;
    selected_onehot = '0;
    for (offset = 0; offset < NUM_SOURCES; offset = offset + 1) begin
      if (!selected_valid && rotated_request[offset]) begin
        selected_valid = 1'b1;
        selected_source = rotation_base + SOURCE_WIDTH'(offset);
        selected_onehot[rotation_base + SOURCE_WIDTH'(offset)] = 1'b1;
      end
    end
  end
endmodule

module a7_replicated_selector_reference #(
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
  /* verilator lint_off UNOPTFLAT */
  wire [RETIRE_LANES:0][NUM_SOURCES-1:0] residual_request;
  /* verilator lint_on UNOPTFLAT */
  wire [RETIRE_LANES-1:0] selected_valid;
  wire [RETIRE_LANES-1:0][SOURCE_WIDTH-1:0] selected_source;
  wire [RETIRE_LANES-1:0][NUM_SOURCES-1:0] selected_onehot;
  logic [RETIRE_LANES-1:0] lane_available;
  logic [RETIRE_LANES-1:0][COUNT_WIDTH-1:0] available_rank;
  logic [COUNT_WIDTH-1:0] available_count;
  logic [RETIRE_LANES-1:0] fill_found;
  logic [RETIRE_LANES-1:0][ADDR_WIDTH-1:0] fill_event;
  logic [RETIRE_LANES-1:0][SOURCE_WIDTH-1:0] fill_source;
  logic [NUM_SOURCES-1:0] inflight_next;
  logic [SOURCE_WIDTH-1:0] last_selected_source;
  logic any_selected;
  integer lane_output;
  integer lane;
  integer lane_seq;
  integer slot;
  integer running_available;
  integer selected_count;

  assign residual_request[0] = eligible_request;
  genvar selector;
  generate
    for (selector = 0; selector < RETIRE_LANES; selector = selector + 1) begin : selectors
      a7_rotating_priority_selector #(
        .NUM_SOURCES(NUM_SOURCES), .SOURCE_WIDTH(SOURCE_WIDTH)
      ) selector_i (
        .request(residual_request[selector]), .rotation_base(rotation_base),
        .selected_valid(selected_valid[selector]),
        .selected_source(selected_source[selector]),
        .selected_onehot(selected_onehot[selector])
      );
      assign residual_request[selector+1] =
        residual_request[selector] & ~selected_onehot[selector];
    end
  endgenerate

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

  always_comb eligible_request =
    source_valid & (~source_inflight | retiring_source);

  always_comb begin
    running_available = 0;
    lane_available = '0;
    for (lane = 0; lane < RETIRE_LANES; lane = lane + 1) begin
      lane_available[lane] = !lane_valid[lane] || retire_ready[lane];
      available_rank[lane] = COUNT_WIDTH'(running_available);
      if (lane_available[lane]) running_available = running_available + 1;
    end
    available_count = COUNT_WIDTH'(running_available);

    source_ready = '0;
    selected_count = 0;
    for (slot = 0; slot < RETIRE_LANES; slot = slot + 1) begin
      if ((COUNT_WIDTH'(slot) < available_count) && selected_valid[slot]) begin
        source_ready = source_ready | selected_onehot[slot];
        selected_count = selected_count + 1;
      end
    end

    fill_found = '0;
    for (lane = 0; lane < RETIRE_LANES; lane = lane + 1) begin
      fill_event[lane] = '0;
      fill_source[lane] = '0;
      if (lane_available[lane]) begin
        for (slot = 0; slot < RETIRE_LANES; slot = slot + 1) begin
          if (!fill_found[lane] &&
              (available_rank[lane] == COUNT_WIDTH'(slot)) &&
              (COUNT_WIDTH'(slot) < available_count) && selected_valid[slot]) begin
            fill_found[lane] = 1'b1;
            fill_source[lane] = selected_source[slot];
            fill_event[lane] = source_event[selected_source[slot]];
          end
        end
      end
    end

    any_selected = (selected_count != 0);
    last_selected_source = rotation_base;
    for (slot = 0; slot < RETIRE_LANES; slot = slot + 1)
      if (selected_valid[slot] && (slot == selected_count-1))
        last_selected_source = selected_source[slot];

    inflight_next = (source_inflight & ~retiring_source) | source_ready;
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
    if ((RETIRE_LANES != 1) && (RETIRE_LANES != 2) &&
        (RETIRE_LANES != 4) && (RETIRE_LANES != 8))
      $fatal(1, "A7 reference supports RETIRE_LANES=1,2,4,8");
    if (RETIRE_LANES > NUM_SOURCES)
      $fatal(1, "A7 reference RETIRE_LANES must not exceed NUM_SOURCES");
  end
endmodule
