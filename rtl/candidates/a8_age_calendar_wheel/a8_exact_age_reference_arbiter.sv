`timescale 1ns/1ps

// Calibration-only exact age reference. This is intentionally not the A8
// candidate: every tracked source counter toggles while it waits.
module a8_exact_age_reference_arbiter #(
  parameter int NUM_SOURCES  = 16,
  parameter int AGE_WIDTH    = $clog2(2 * NUM_SOURCES),
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (
  input  logic                   clk,
  input  logic                   rst_n,
  input  logic [NUM_SOURCES-1:0] request,
  input  logic                   advance,
  output logic [NUM_SOURCES-1:0] grant,
  output logic [NUM_SOURCES-1:0] tracked_debug
);
  logic [NUM_SOURCES-1:0] tracked;
  logic [AGE_WIDTH-1:0] age [NUM_SOURCES];
  logic [SOURCE_WIDTH-1:0] tie_start;
  logic [AGE_WIDTH-1:0] oldest_age;
  logic oldest_valid;
  logic source_found;
  integer source_index;
  integer source_offset;
  integer sequential_source;

  initial begin
    if (NUM_SOURCES < 1)
      $fatal(1, "A8 exact reference NUM_SOURCES must be positive");
    if (AGE_WIDTH < 1)
      $fatal(1, "A8 exact reference AGE_WIDTH must be positive");
    if ((1 << AGE_WIDTH) < (2 * NUM_SOURCES))
      $fatal(1, "A8 exact reference counter range must cover 2*NUM_SOURCES");
  end

  always_comb begin
    oldest_age = '0;
    oldest_valid = 1'b0;
    for (source_index = 0; source_index < NUM_SOURCES;
         source_index = source_index + 1) begin
      if (request[source_index] &&
          (!oldest_valid ||
           ((tracked[source_index] ? age[source_index] : '0) > oldest_age))) begin
        oldest_age = tracked[source_index] ? age[source_index] : '0;
        oldest_valid = 1'b1;
      end
    end

    grant = '0;
    source_found = 1'b0;
    if (advance && oldest_valid) begin
      for (source_offset = 0; source_offset < NUM_SOURCES;
           source_offset = source_offset + 1) begin
        source_index = integer'(tie_start) + source_offset;
        if (source_index >= NUM_SOURCES)
          source_index = source_index - NUM_SOURCES;
        if (!source_found && request[source_index] &&
            ((tracked[source_index] ? age[source_index] : '0) == oldest_age)) begin
          grant[source_index] = 1'b1;
          source_found = 1'b1;
        end
      end
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      tracked <= '0;
      tie_start <= '0;
      for (sequential_source = 0; sequential_source < NUM_SOURCES;
           sequential_source = sequential_source + 1)
        age[sequential_source] <= '0;
    end else begin
      for (sequential_source = 0; sequential_source < NUM_SOURCES;
           sequential_source = sequential_source + 1) begin
        if (!request[sequential_source] || grant[sequential_source]) begin
          tracked[sequential_source] <= 1'b0;
          age[sequential_source] <= '0;
        end else if (!tracked[sequential_source]) begin
          tracked[sequential_source] <= 1'b1;
          age[sequential_source] <= '0;
        end else if (age[sequential_source] != {AGE_WIDTH{1'b1}}) begin
          age[sequential_source] <= age[sequential_source] + 1'b1;
        end
      end

      if (|grant) begin
        if (grant[NUM_SOURCES-1])
          tie_start <= '0;
        else begin
          for (sequential_source = 0; sequential_source < NUM_SOURCES-1;
               sequential_source = sequential_source + 1)
            if (grant[sequential_source])
              tie_start <= SOURCE_WIDTH'(sequential_source + 1);
        end
      end
    end
  end

  assign tracked_debug = tracked;
endmodule
