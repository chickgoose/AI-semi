`timescale 1ns/1ps

// Calibration-only RR selector factored out for like-for-like logic-depth proxy.
module a8_rr_reference_arbiter #(
  parameter int NUM_SOURCES = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (
  input  logic                   clk,
  input  logic                   rst_n,
  input  logic [NUM_SOURCES-1:0] request,
  input  logic                   advance,
  output logic [NUM_SOURCES-1:0] grant
);
  logic [SOURCE_WIDTH-1:0] rr_start;
  integer source_index;
  integer source_offset;
  integer sequential_source;
  logic source_found;

  always_comb begin
    grant = '0;
    source_found = 1'b0;
    for (source_offset = 0; source_offset < NUM_SOURCES;
         source_offset = source_offset + 1) begin
      source_index = integer'(rr_start) + source_offset;
      if (source_index >= NUM_SOURCES)
        source_index = source_index - NUM_SOURCES;
      if (advance && !source_found && request[source_index]) begin
        grant[source_index] = 1'b1;
        source_found = 1'b1;
      end
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rr_start <= '0;
    end else if (|grant) begin
      if (grant[NUM_SOURCES-1])
        rr_start <= '0;
      else begin
        for (sequential_source = 0; sequential_source < NUM_SOURCES-1;
             sequential_source = sequential_source + 1)
          if (grant[sequential_source])
            rr_start <= SOURCE_WIDTH'(sequential_source + 1);
      end
    end
  end
endmodule
