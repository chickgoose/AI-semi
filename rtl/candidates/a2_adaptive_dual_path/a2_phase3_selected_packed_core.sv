`timescale 1ns/1ps

// Packed-port mirror of the fixed phase-2 selection for local Yosys, whose
// Verilog frontend cannot parse the normalized unpacked-array event port.
module a2_phase3_selected_packed_core #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (
  input  logic clk_i,
  input  logic rst_ni,
  input  logic [NUM_SOURCES-1:0] source_valid_i,
  output logic [NUM_SOURCES-1:0] source_ready_o,
  input  logic [NUM_SOURCES*ADDR_WIDTH-1:0] source_event_i,
  output logic retire_valid_o,
  input  logic retire_ready_i,
  output logic [ADDR_WIDTH-1:0] retire_event_o,
  output logic [SOURCE_WIDTH-1:0] retire_source_o
);
  localparam int RESERVOIR_DEPTH = 16;
  localparam int BANK_COUNT = 4;
  localparam int ENTER_LEVEL = 4;
  localparam int EXIT_LEVEL = 0;
  localparam int QUIET_CYCLES = 1;
  localparam int BANK_DEPTH = 4;
  localparam int PTR_WIDTH = 4;
  localparam int COUNT_WIDTH = 5;
  localparam int BANK_WIDTH = 2;
  localparam int ROW_WIDTH = 2;

  logic [ADDR_WIDTH-1:0] bank_event [BANK_COUNT][BANK_DEPTH];
  logic [SOURCE_WIDTH-1:0] bank_source [BANK_COUNT][BANK_DEPTH];
  logic [PTR_WIDTH-1:0] read_pointer;
  logic [PTR_WIDTH-1:0] write_pointer;
  logic [COUNT_WIDTH-1:0] reservoir_count;
  logic [COUNT_WIDTH-1:0] previous_count;
  logic [SOURCE_WIDTH-1:0] rotate_base;
  logic quiet_count;
  logic burst_mode;
  logic queue_pop;
  logic wide_admission;
  logic enqueue_valid [BANK_COUNT];
  logic [ADDR_WIDTH-1:0] enqueue_event [BANK_COUNT];
  logic [SOURCE_WIDTH-1:0] enqueue_source [BANK_COUNT];
  logic [PTR_WIDTH-1:0] lane_write_pointer [BANK_COUNT];
  logic [BANK_WIDTH-1:0] lane_write_bank [BANK_COUNT];
  logic [ROW_WIDTH-1:0] lane_write_row [BANK_COUNT];
  logic [BANK_WIDTH-1:0] read_bank;
  logic [ROW_WIDTH-1:0] read_row;
  integer enqueue_index [BANK_COUNT];
  integer valid_count;
  integer free_slots;
  integer queue_accept_limit;
  integer direct_source;
  integer scan_offset;
  integer scan_source;
  integer lane;
  integer write_lane;
  integer lane_count;
  integer enqueue_count;
  integer next_occupancy;

`ifndef SYNTHESIS
  logic [RESERVOIR_DEPTH*ADDR_WIDTH-1:0] vcd_bank_event;
  logic [RESERVOIR_DEPTH*SOURCE_WIDTH-1:0] vcd_bank_source;
  integer vcd_bank;
  integer vcd_row;
  always_comb begin
    vcd_bank_event = '0;
    vcd_bank_source = '0;
    for (vcd_bank = 0; vcd_bank < BANK_COUNT; vcd_bank = vcd_bank + 1)
      for (vcd_row = 0; vcd_row < BANK_DEPTH; vcd_row = vcd_row + 1) begin
        vcd_bank_event[(vcd_bank*BANK_DEPTH+vcd_row)*ADDR_WIDTH +: ADDR_WIDTH] =
          bank_event[vcd_bank][vcd_row];
        vcd_bank_source[(vcd_bank*BANK_DEPTH+vcd_row)*SOURCE_WIDTH +: SOURCE_WIDTH] =
          bank_source[vcd_bank][vcd_row];
      end
  end
`endif

  always_comb begin
    valid_count = 0;
    for (scan_offset = 0; scan_offset < NUM_SOURCES; scan_offset = scan_offset + 1)
      if (source_valid_i[scan_offset])
        valid_count = valid_count + 1;

    queue_pop = (reservoir_count != 0) && retire_ready_i;
    read_bank = BANK_WIDTH'(integer'(read_pointer) % BANK_COUNT);
    read_row = ROW_WIDTH'(integer'(read_pointer) / BANK_COUNT);
    for (lane = 0; lane < BANK_COUNT; lane = lane + 1) begin
      lane_write_pointer[lane] = write_pointer + PTR_WIDTH'(lane);
      lane_write_bank[lane] =
        BANK_WIDTH'(integer'(lane_write_pointer[lane]) % BANK_COUNT);
      lane_write_row[lane] =
        ROW_WIDTH'(integer'(lane_write_pointer[lane]) / BANK_COUNT);
    end

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

    wide_admission = burst_mode || (valid_count >= 2) ||
                     (reservoir_count > previous_count) ||
                     (integer'(reservoir_count) >= ENTER_LEVEL);
    queue_accept_limit = 0;
    if (reservoir_count != 0) begin
      if (wide_admission)
        queue_accept_limit = (free_slots >= BANK_COUNT) ? BANK_COUNT : free_slots;
      else
        queue_accept_limit = (free_slots >= 1) ? 1 : 0;
    end else if ((direct_source >= 0) && retire_ready_i && (valid_count >= 2)) begin
      queue_accept_limit = (free_slots >= BANK_COUNT) ? BANK_COUNT : free_slots;
    end

    for (lane = 0; lane < BANK_COUNT; lane = lane + 1) begin
      enqueue_index[lane] = -1;
      enqueue_valid[lane] = 1'b0;
      enqueue_event[lane] = '0;
      enqueue_source[lane] = '0;
    end
    lane_count = 0;
    for (scan_offset = 0; scan_offset < NUM_SOURCES; scan_offset = scan_offset + 1) begin
      scan_source = integer'(rotate_base) + scan_offset;
      if (scan_source >= NUM_SOURCES)
        scan_source = scan_source - NUM_SOURCES;
      if (source_valid_i[scan_source] && (scan_source != direct_source) &&
          (lane_count < queue_accept_limit)) begin
        enqueue_index[lane_count] = scan_source;
        lane_count = lane_count + 1;
      end
    end
    for (lane = 0; lane < BANK_COUNT; lane = lane + 1) begin
      enqueue_valid[lane] = enqueue_index[lane] >= 0;
      if (enqueue_valid[lane]) begin
        enqueue_event[lane] =
          source_event_i[enqueue_index[lane]*ADDR_WIDTH +: ADDR_WIDTH];
        enqueue_source[lane] = SOURCE_WIDTH'(enqueue_index[lane]);
      end
    end

    source_ready_o = '0;
    if ((direct_source >= 0) && retire_ready_i)
      source_ready_o[direct_source] = 1'b1;
    for (lane = 0; lane < BANK_COUNT; lane = lane + 1)
      if (enqueue_valid[lane])
        source_ready_o[enqueue_index[lane]] = 1'b1;

    retire_valid_o = 1'b0;
    retire_event_o = '0;
    retire_source_o = '0;
    if (reservoir_count != 0) begin
      retire_valid_o = 1'b1;
      retire_event_o = bank_event[read_bank][read_row];
      retire_source_o = bank_source[read_bank][read_row];
    end else if (direct_source >= 0) begin
      retire_valid_o = 1'b1;
      retire_event_o =
        source_event_i[direct_source*ADDR_WIDTH +: ADDR_WIDTH];
      retire_source_o = SOURCE_WIDTH'(direct_source);
    end

    enqueue_count = 0;
    for (lane = 0; lane < BANK_COUNT; lane = lane + 1)
      if (enqueue_valid[lane])
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
      quiet_count <= 1'b0;
      burst_mode <= 1'b0;
    end else begin
      if (queue_pop)
        read_pointer <= read_pointer + PTR_WIDTH'(1);
      for (write_lane = 0; write_lane < BANK_COUNT;
           write_lane = write_lane + 1) begin
        if (enqueue_valid[write_lane]) begin
          bank_event[lane_write_bank[write_lane]][lane_write_row[write_lane]] <=
            enqueue_event[write_lane];
          bank_source[lane_write_bank[write_lane]][lane_write_row[write_lane]] <=
            enqueue_source[write_lane];
        end
      end
      if (enqueue_count != 0)
        write_pointer <= write_pointer + PTR_WIDTH'(enqueue_count);
      reservoir_count <= COUNT_WIDTH'(next_occupancy);
      previous_count <= reservoir_count;

      if (enqueue_count != 0) begin
        if (enqueue_index[enqueue_count-1] == NUM_SOURCES-1)
          rotate_base <= '0;
        else
          rotate_base <= SOURCE_WIDTH'(enqueue_index[enqueue_count-1] + 1);
      end else if ((direct_source >= 0) && retire_ready_i) begin
        if (direct_source == NUM_SOURCES-1)
          rotate_base <= '0;
        else
          rotate_base <= SOURCE_WIDTH'(direct_source + 1);
      end

      if (!burst_mode) begin
        quiet_count <= 1'b0;
        if ((valid_count >= 2) || (integer'(reservoir_count) >= ENTER_LEVEL) ||
            (reservoir_count > previous_count))
          burst_mode <= 1'b1;
      end else if ((integer'(reservoir_count) <= EXIT_LEVEL) &&
                   (reservoir_count <= previous_count) &&
                   (valid_count < 2)) begin
        if (integer'(quiet_count) >= QUIET_CYCLES-1) begin
          burst_mode <= 1'b0;
          quiet_count <= 1'b0;
        end else begin
          quiet_count <= 1'b1;
        end
      end else begin
        quiet_count <= 1'b0;
      end
    end
  end
endmodule
