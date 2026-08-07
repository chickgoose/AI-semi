`timescale 1ns/1ps

module a2_phase3_flat_rr_core #(
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
  logic [SOURCE_WIDTH-1:0] rotate_base;
  integer offset;
  integer scan_source;
  integer selected_source;

  always_comb begin
    selected_source = -1;
    for (offset = 0; offset < NUM_SOURCES; offset = offset + 1) begin
      scan_source = integer'(rotate_base) + offset;
      if (scan_source >= NUM_SOURCES)
        scan_source = scan_source - NUM_SOURCES;
      if ((selected_source < 0) && source_valid_i[scan_source])
        selected_source = scan_source;
    end
    source_ready_o = '0;
    retire_valid_o = selected_source >= 0;
    retire_event_o = '0;
    retire_source_o = '0;
    if (selected_source >= 0) begin
      retire_event_o = source_event_i[selected_source*ADDR_WIDTH +: ADDR_WIDTH];
      retire_source_o = SOURCE_WIDTH'(selected_source);
      if (retire_ready_i)
        source_ready_o[selected_source] = 1'b1;
    end
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      rotate_base <= '0;
    end else if ((selected_source >= 0) && retire_ready_i) begin
      if (selected_source == NUM_SOURCES-1)
        rotate_base <= '0;
      else
        rotate_base <= SOURCE_WIDTH'(selected_source + 1);
    end
  end
endmodule

module a2_phase3_always_buffered_core #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int RESERVOIR_DEPTH = 16,
  parameter int BANK_COUNT = 4,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES),
  parameter int PTR_WIDTH = $clog2(RESERVOIR_DEPTH),
  parameter int COUNT_WIDTH = $clog2(RESERVOIR_DEPTH + 1)
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
  localparam int BANK_DEPTH = RESERVOIR_DEPTH / BANK_COUNT;
  localparam int BANK_WIDTH = $clog2(BANK_COUNT);
  localparam int ROW_WIDTH = $clog2(BANK_DEPTH);

  logic [ADDR_WIDTH-1:0] bank_event [BANK_COUNT][BANK_DEPTH];
  logic [SOURCE_WIDTH-1:0] bank_source [BANK_COUNT][BANK_DEPTH];
  logic [PTR_WIDTH-1:0] read_pointer;
  logic [PTR_WIDTH-1:0] write_pointer;
  logic [COUNT_WIDTH-1:0] count;
  logic [SOURCE_WIDTH-1:0] rotate_base;
  logic pop;
  logic enqueue_valid [BANK_COUNT];
  logic [ADDR_WIDTH-1:0] enqueue_event [BANK_COUNT];
  logic [SOURCE_WIDTH-1:0] enqueue_source [BANK_COUNT];
  logic [PTR_WIDTH-1:0] lane_pointer [BANK_COUNT];
  logic [BANK_WIDTH-1:0] lane_bank [BANK_COUNT];
  logic [ROW_WIDTH-1:0] lane_row [BANK_COUNT];
  logic [BANK_WIDTH-1:0] read_bank;
  logic [ROW_WIDTH-1:0] read_row;
  integer enqueue_index [BANK_COUNT];
  integer free_slots;
  integer accept_limit;
  integer offset;
  integer scan_source;
  integer lane;
  integer write_lane;
  integer lane_count;
  integer enqueue_count;
  integer next_count;

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
    pop = (count != 0) && retire_ready_i;
    read_bank = BANK_WIDTH'(integer'(read_pointer) % BANK_COUNT);
    read_row = ROW_WIDTH'(integer'(read_pointer) / BANK_COUNT);
    free_slots = RESERVOIR_DEPTH - integer'(count);
    if (pop)
      free_slots = free_slots + 1;
    accept_limit = (free_slots >= BANK_COUNT) ? BANK_COUNT : free_slots;

    for (lane = 0; lane < BANK_COUNT; lane = lane + 1) begin
      lane_pointer[lane] = write_pointer + PTR_WIDTH'(lane);
      lane_bank[lane] = BANK_WIDTH'(integer'(lane_pointer[lane]) % BANK_COUNT);
      lane_row[lane] = ROW_WIDTH'(integer'(lane_pointer[lane]) / BANK_COUNT);
      enqueue_index[lane] = -1;
      enqueue_valid[lane] = 1'b0;
      enqueue_event[lane] = '0;
      enqueue_source[lane] = '0;
    end

    lane_count = 0;
    for (offset = 0; offset < NUM_SOURCES; offset = offset + 1) begin
      scan_source = integer'(rotate_base) + offset;
      if (scan_source >= NUM_SOURCES)
        scan_source = scan_source - NUM_SOURCES;
      if (source_valid_i[scan_source] && (lane_count < accept_limit)) begin
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
    for (lane = 0; lane < BANK_COUNT; lane = lane + 1)
      if (enqueue_valid[lane])
        source_ready_o[enqueue_index[lane]] = 1'b1;

    retire_valid_o = count != 0;
    retire_event_o = '0;
    retire_source_o = '0;
    if (count != 0) begin
      retire_event_o = bank_event[read_bank][read_row];
      retire_source_o = bank_source[read_bank][read_row];
    end

    enqueue_count = 0;
    for (lane = 0; lane < BANK_COUNT; lane = lane + 1)
      if (enqueue_valid[lane])
        enqueue_count = enqueue_count + 1;
    next_count = integer'(count) + enqueue_count;
    if (pop)
      next_count = next_count - 1;
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      read_pointer <= '0;
      write_pointer <= '0;
      count <= '0;
      rotate_base <= '0;
    end else begin
      if (pop)
        read_pointer <= read_pointer + PTR_WIDTH'(1);
      for (write_lane = 0; write_lane < BANK_COUNT;
           write_lane = write_lane + 1) begin
        if (enqueue_valid[write_lane]) begin
          bank_event[lane_bank[write_lane]][lane_row[write_lane]] <=
            enqueue_event[write_lane];
          bank_source[lane_bank[write_lane]][lane_row[write_lane]] <=
            enqueue_source[write_lane];
        end
      end
      if (enqueue_count != 0) begin
        write_pointer <= write_pointer + PTR_WIDTH'(enqueue_count);
        if (enqueue_index[enqueue_count-1] == NUM_SOURCES-1)
          rotate_base <= '0;
        else
          rotate_base <= SOURCE_WIDTH'(enqueue_index[enqueue_count-1] + 1);
      end
      count <= COUNT_WIDTH'(next_count);
    end
  end
endmodule
