`timescale 1ns/1ps

module a2_adaptive_dual_path_core #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int RESERVOIR_DEPTH = 8,
  parameter int ENTER_LEVEL = 4,
  parameter int EXIT_LEVEL = 1,
  parameter int QUIET_CYCLES = 3,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES),
  parameter int PTR_WIDTH = (RESERVOIR_DEPTH <= 1) ? 1 : $clog2(RESERVOIR_DEPTH),
  parameter int COUNT_WIDTH = $clog2(RESERVOIR_DEPTH + 1),
  parameter int QUIET_WIDTH = (QUIET_CYCLES <= 1) ? 1 : $clog2(QUIET_CYCLES + 1)
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
  localparam int BANK_DEPTH = RESERVOIR_DEPTH / 2;
  localparam int ROW_WIDTH = (BANK_DEPTH <= 1) ? 1 : $clog2(BANK_DEPTH);

  logic [ADDR_WIDTH-1:0] bank0_event [BANK_DEPTH];
  logic [ADDR_WIDTH-1:0] bank1_event [BANK_DEPTH];
  logic [SOURCE_WIDTH-1:0] bank0_source [BANK_DEPTH];
  logic [SOURCE_WIDTH-1:0] bank1_source [BANK_DEPTH];

  logic [PTR_WIDTH-1:0] read_pointer;
  logic [PTR_WIDTH-1:0] write_pointer;
  logic [COUNT_WIDTH-1:0] reservoir_count;
  logic [COUNT_WIDTH-1:0] previous_count;
  logic [SOURCE_WIDTH-1:0] rotate_base;
  logic [QUIET_WIDTH-1:0] quiet_count;
  logic burst_mode;

  logic queue_pop;
  logic wide_admission;
  logic enqueue0_valid;
  logic enqueue1_valid;
  logic [ADDR_WIDTH-1:0] enqueue0_event;
  logic [ADDR_WIDTH-1:0] enqueue1_event;
  logic [SOURCE_WIDTH-1:0] enqueue0_source;
  logic [SOURCE_WIDTH-1:0] enqueue1_source;
  logic [PTR_WIDTH-1:0] write_pointer_one;
  logic [ROW_WIDTH-1:0] read_row;
  logic [ROW_WIDTH-1:0] write_row_zero;
  logic [ROW_WIDTH-1:0] write_row_one;
  integer valid_count;
  integer free_slots;
  integer queue_accept_limit;
  integer direct_source;
  integer enqueue0_index;
  integer enqueue1_index;
  integer scan_offset;
  integer scan_source;
  integer lane_count;
  integer enqueue_count;
  integer next_occupancy;

  always_comb begin
    valid_count = 0;
    for (scan_offset = 0; scan_offset < NUM_SOURCES; scan_offset = scan_offset + 1)
      if (source_valid_i[scan_offset])
        valid_count = valid_count + 1;

    queue_pop = (reservoir_count != 0) && retire_ready_i;
    write_pointer_one = write_pointer + PTR_WIDTH'(1);
    read_row = ROW_WIDTH'(read_pointer >> 1);
    write_row_zero = ROW_WIDTH'(write_pointer >> 1);
    write_row_one = ROW_WIDTH'(write_pointer_one >> 1);
    free_slots = RESERVOIR_DEPTH - integer'(reservoir_count);
    if (queue_pop)
      free_slots = free_slots + 1;

    direct_source = -1;
    if (reservoir_count == 0) begin
      for (scan_offset = 0; scan_offset < NUM_SOURCES; scan_offset = scan_offset + 1) begin
        scan_source = integer'(rotate_base) + scan_offset;
        if (scan_source >= NUM_SOURCES)
          scan_source = scan_source - NUM_SOURCES;
        if ((direct_source < 0) && source_valid_i[scan_source])
          direct_source = scan_source;
      end
    end

    queue_accept_limit = 0;
    wide_admission = burst_mode || (valid_count >= 2) ||
                     (reservoir_count > previous_count) ||
                     (integer'(reservoir_count) >= ENTER_LEVEL);
    if (reservoir_count != 0) begin
      if (wide_admission)
        queue_accept_limit = (free_slots >= 2) ? 2 : free_slots;
      else
        queue_accept_limit = (free_slots >= 1) ? 1 : 0;
    end else if ((direct_source >= 0) && retire_ready_i && (valid_count >= 2)) begin
      // Immediate fan-in activates the reservoir without waiting for the mode
      // register. The bypassed event is older than both queued selections.
      queue_accept_limit = (free_slots >= 2) ? 2 : free_slots;
    end

    enqueue0_index = -1;
    enqueue1_index = -1;
    lane_count = 0;
    for (scan_offset = 0; scan_offset < NUM_SOURCES; scan_offset = scan_offset + 1) begin
      scan_source = integer'(rotate_base) + scan_offset;
      if (scan_source >= NUM_SOURCES)
        scan_source = scan_source - NUM_SOURCES;
      if (source_valid_i[scan_source] && (scan_source != direct_source) &&
          (lane_count < queue_accept_limit)) begin
        if (lane_count == 0)
          enqueue0_index = scan_source;
        else
          enqueue1_index = scan_source;
        lane_count = lane_count + 1;
      end
    end

    enqueue0_valid = enqueue0_index >= 0;
    enqueue1_valid = enqueue1_index >= 0;
    enqueue0_event = '0;
    enqueue1_event = '0;
    enqueue0_source = '0;
    enqueue1_source = '0;
    if (enqueue0_valid) begin
      enqueue0_event = source_event_i[enqueue0_index];
      enqueue0_source = SOURCE_WIDTH'(enqueue0_index);
    end
    if (enqueue1_valid) begin
      enqueue1_event = source_event_i[enqueue1_index];
      enqueue1_source = SOURCE_WIDTH'(enqueue1_index);
    end

    source_ready_o = '0;
    if ((direct_source >= 0) && retire_ready_i)
      source_ready_o[direct_source] = 1'b1;
    if (enqueue0_valid)
      source_ready_o[enqueue0_index] = 1'b1;
    if (enqueue1_valid)
      source_ready_o[enqueue1_index] = 1'b1;

    retire_valid_o = 1'b0;
    retire_event_o = '0;
    retire_source_o = '0;
    if (reservoir_count != 0) begin
      retire_valid_o = 1'b1;
      if (read_pointer[0] == 1'b0) begin
        retire_event_o = bank0_event[read_row];
        retire_source_o = bank0_source[read_row];
      end else begin
        retire_event_o = bank1_event[read_row];
        retire_source_o = bank1_source[read_row];
      end
    end else if (direct_source >= 0) begin
      retire_valid_o = 1'b1;
      retire_event_o = source_event_i[direct_source];
      retire_source_o = SOURCE_WIDTH'(direct_source);
    end

    enqueue_count = 0;
    if (enqueue0_valid)
      enqueue_count = enqueue_count + 1;
    if (enqueue1_valid)
      enqueue_count = enqueue_count + 1;
    next_occupancy = integer'(reservoir_count) + enqueue_count;
    if (queue_pop)
      next_occupancy = next_occupancy - 1;
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      read_pointer <= '0;
      write_pointer <= '0;
      reservoir_count <= '0;
      previous_count <= '0;
      rotate_base <= '0;
      quiet_count <= '0;
      burst_mode <= 1'b0;
    end else begin
      if (queue_pop)
        read_pointer <= read_pointer + PTR_WIDTH'(1);

      if (enqueue0_valid) begin
        if (write_pointer[0] == 1'b0) begin
          bank0_event[write_row_zero] <= enqueue0_event;
          bank0_source[write_row_zero] <= enqueue0_source;
        end else begin
          bank1_event[write_row_zero] <= enqueue0_event;
          bank1_source[write_row_zero] <= enqueue0_source;
        end
      end
      if (enqueue1_valid) begin
        if (write_pointer_one[0] == 1'b0) begin
          bank0_event[write_row_one] <= enqueue1_event;
          bank0_source[write_row_one] <= enqueue1_source;
        end else begin
          bank1_event[write_row_one] <= enqueue1_event;
          bank1_source[write_row_one] <= enqueue1_source;
        end
      end
      if (enqueue_count != 0)
        write_pointer <= write_pointer + PTR_WIDTH'(enqueue_count);

      reservoir_count <= COUNT_WIDTH'(next_occupancy);
      previous_count <= reservoir_count;

      if (enqueue1_valid) begin
        if (enqueue1_index == NUM_SOURCES-1)
          rotate_base <= '0;
        else
          rotate_base <= SOURCE_WIDTH'(enqueue1_index + 1);
      end else if (enqueue0_valid) begin
        if (enqueue0_index == NUM_SOURCES-1)
          rotate_base <= '0;
        else
          rotate_base <= SOURCE_WIDTH'(enqueue0_index + 1);
      end else if ((direct_source >= 0) && retire_ready_i) begin
        if (direct_source == NUM_SOURCES-1)
          rotate_base <= '0;
        else
          rotate_base <= SOURCE_WIDTH'(direct_source + 1);
      end

      if (!burst_mode) begin
        quiet_count <= '0;
        if ((valid_count >= 2) || (integer'(reservoir_count) >= ENTER_LEVEL) ||
            (reservoir_count > previous_count))
          burst_mode <= 1'b1;
      end else if ((integer'(reservoir_count) <= EXIT_LEVEL) &&
                   (reservoir_count <= previous_count) &&
                   (valid_count < 2)) begin
        if (integer'(quiet_count) >= QUIET_CYCLES-1) begin
          burst_mode <= 1'b0;
          quiet_count <= '0;
        end else begin
          quiet_count <= quiet_count + QUIET_WIDTH'(1);
        end
      end else begin
        quiet_count <= '0;
      end
    end
  end

`ifndef SYNTHESIS
  initial begin
    if (NUM_SOURCES < 1)
      $fatal(1, "A2 NUM_SOURCES must be positive");
    if ((RESERVOIR_DEPTH < 2) ||
        ((RESERVOIR_DEPTH & (RESERVOIR_DEPTH-1)) != 0))
      $fatal(1, "A2 RESERVOIR_DEPTH must be an even power of two");
    if ((ENTER_LEVEL > RESERVOIR_DEPTH) || (EXIT_LEVEL >= ENTER_LEVEL))
      $fatal(1, "A2 hysteresis levels are invalid");
    if (QUIET_CYCLES < 1)
      $fatal(1, "A2 QUIET_CYCLES must be positive");
  end

  always_ff @(posedge clk_i) begin
    if (rst_ni) begin
      if (integer'(reservoir_count) > RESERVOIR_DEPTH)
        $error("A2 reservoir overflow count=%0d", reservoir_count);
      if ((source_ready_o & ~source_valid_i) != '0)
        $error("A2 ready without a valid source");
      if ($countones(source_ready_o) > 3)
        $error("A2 accepted more than direct plus two bank writes");
    end
  end
`endif
endmodule
