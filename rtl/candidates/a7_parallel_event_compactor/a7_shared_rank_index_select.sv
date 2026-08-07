`timescale 1ns/1ps

// Converts one shared population prefix into the first K cyclic indices.
// This is rank matching, not K cascaded priority selectors.
module a7_shared_rank_index_select #(
  parameter int NUM_SOURCES = 16,
  parameter int SELECT_LANES = 4,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES),
  parameter int COUNT_WIDTH = $clog2(NUM_SOURCES + 1)
) (
  input  logic [NUM_SOURCES-1:0] request,
  input  logic [NUM_SOURCES-1:0][COUNT_WIDTH-1:0] inclusive_count,
  input  logic [COUNT_WIDTH-1:0] total_count,
  input  logic [SOURCE_WIDTH-1:0] rotation_base,
  input  logic [COUNT_WIDTH-1:0] select_limit,
  output logic [SELECT_LANES-1:0] selected_valid,
  output logic [SELECT_LANES-1:0][SOURCE_WIDTH-1:0] selected_index,
  output logic [NUM_SOURCES-1:0] selected_onehot
);
  logic [COUNT_WIDTH-1:0] base_before;
  logic [NUM_SOURCES-1:0][COUNT_WIDTH-1:0] cyclic_rank;
  integer source;
  integer slot;

  always_comb begin
    if (rotation_base == '0)
      base_before = '0;
    else
      base_before = inclusive_count[rotation_base-1];
    for (source = 0; source < NUM_SOURCES; source = source + 1) begin
      if (SOURCE_WIDTH'(source) >= rotation_base)
        cyclic_rank[source] =
          (inclusive_count[source] - COUNT_WIDTH'(request[source])) -
          base_before;
      else
        cyclic_rank[source] =
          (total_count - base_before) +
          (inclusive_count[source] - COUNT_WIDTH'(request[source]));
    end

    selected_valid = '0;
    selected_index = '0;
    selected_onehot = '0;
    for (slot = 0; slot < SELECT_LANES; slot = slot + 1) begin
      if (COUNT_WIDTH'(slot) < select_limit) begin
        for (source = 0; source < NUM_SOURCES; source = source + 1) begin
          if (!selected_valid[slot] && request[source] &&
              (cyclic_rank[source] == COUNT_WIDTH'(slot))) begin
            selected_valid[slot] = 1'b1;
            selected_index[slot] = SOURCE_WIDTH'(source);
            selected_onehot[source] = 1'b1;
          end
        end
      end
    end
  end
endmodule
