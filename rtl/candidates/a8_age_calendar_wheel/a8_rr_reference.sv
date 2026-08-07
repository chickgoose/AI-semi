`timescale 1ns/1ps

// Calibration-only RR reference with the same single-lane output register.
module a8_rr_reference #(
  parameter int NUM_SOURCES  = 16,
  parameter int ADDR_WIDTH   = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (
  input  logic                    clk,
  input  logic                    rst_n,
  input  logic [NUM_SOURCES-1:0]  source_valid,
  input  logic [ADDR_WIDTH-1:0]   source_event [NUM_SOURCES],
  output logic [NUM_SOURCES-1:0]  source_ready,
  output logic                    retire_valid,
  output logic [ADDR_WIDTH-1:0]   retire_event,
  output logic [SOURCE_WIDTH-1:0] retire_source
);
  logic [SOURCE_WIDTH-1:0] rr_start;
  logic [SOURCE_WIDTH-1:0] selected_source;
  logic selected_valid;
  integer source_index;
  integer source_offset;

  always_comb begin
    source_ready = '0;
    selected_source = '0;
    selected_valid = 1'b0;
    for (source_offset = 0; source_offset < NUM_SOURCES;
         source_offset = source_offset + 1) begin
      source_index = integer'(rr_start) + source_offset;
      if (source_index >= NUM_SOURCES)
        source_index = source_index - NUM_SOURCES;
      if (!selected_valid && source_valid[source_index]) begin
        selected_source = SOURCE_WIDTH'(source_index);
        selected_valid = 1'b1;
        source_ready[source_index] = 1'b1;
      end
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rr_start <= '0;
      retire_valid <= 1'b0;
      retire_event <= '0;
      retire_source <= '0;
    end else begin
      retire_valid <= selected_valid;
      if (selected_valid) begin
        retire_event <= source_event[selected_source];
        retire_source <= selected_source;
        if (selected_source == SOURCE_WIDTH'(NUM_SOURCES - 1))
          rr_start <= '0;
        else
          rr_start <= selected_source + 1'b1;
      end
    end
  end
endmodule
