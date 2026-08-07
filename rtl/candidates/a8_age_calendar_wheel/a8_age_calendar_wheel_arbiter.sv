`timescale 1ns/1ps

module a8_age_calendar_wheel_arbiter #(
  parameter int NUM_SOURCES   = 16,
  parameter int BUCKET_CYCLES = 4,
  parameter int EPOCH_COUNT   = 8,
  parameter int SOURCE_WIDTH  = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES),
  parameter int EPOCH_WIDTH   = (EPOCH_COUNT <= 1) ? 1 : $clog2(EPOCH_COUNT),
  parameter int PHASE_WIDTH   = (BUCKET_CYCLES <= 1) ? 1 : $clog2(BUCKET_CYCLES)
) (
  input  logic                    clk,
  input  logic                    rst_n,
  input  logic [NUM_SOURCES-1:0]  request,
  input  logic                    advance,
  output logic [NUM_SOURCES-1:0]  grant,
  output logic [NUM_SOURCES-1:0]  tracked_debug,
  output logic [EPOCH_WIDTH-1:0]  epoch_debug
);
  logic [NUM_SOURCES-1:0] tracked;
  logic [EPOCH_WIDTH-1:0] tag [NUM_SOURCES];
  logic [EPOCH_WIDTH-1:0] current_epoch;
  logic [PHASE_WIDTH-1:0] bucket_phase;
  logic [SOURCE_WIDTH-1:0] tie_start;

  logic [EPOCH_COUNT-1:0] bucket_nonempty;
  logic [EPOCH_WIDTH-1:0] oldest_bucket;
  logic oldest_bucket_valid;
  logic source_found;
  integer source_index;
  integer source_offset;
  integer bucket_offset;
  integer candidate_age;
  integer candidate_bucket;
  integer sequential_source;

  initial begin
    if (NUM_SOURCES < 1)
      $fatal(1, "A8 NUM_SOURCES must be positive");
    if (BUCKET_CYCLES < 1)
      $fatal(1, "A8 BUCKET_CYCLES must be positive");
    if ((EPOCH_COUNT < 2) || ((EPOCH_COUNT & (EPOCH_COUNT - 1)) != 0))
      $fatal(1, "A8 EPOCH_COUNT must be a power of two >= 2");
    if ((EPOCH_COUNT * BUCKET_CYCLES) <= (NUM_SOURCES - 1))
      $fatal(1, "A8 wheel horizon must exceed NUM_SOURCES-1");
  end

  always_comb begin
    bucket_nonempty = '0;
    for (source_index = 0; source_index < NUM_SOURCES;
         source_index = source_index + 1) begin
      if (request[source_index]) begin
        if (tracked[source_index])
          bucket_nonempty[tag[source_index]] = 1'b1;
        else
          bucket_nonempty[current_epoch] = 1'b1;
      end
    end

    oldest_bucket = '0;
    oldest_bucket_valid = 1'b0;
    for (bucket_offset = 0; bucket_offset < EPOCH_COUNT;
         bucket_offset = bucket_offset + 1) begin
      candidate_age = EPOCH_COUNT - 1 - bucket_offset;
      candidate_bucket = integer'(current_epoch) + EPOCH_COUNT - candidate_age;
      if (candidate_bucket >= EPOCH_COUNT)
        candidate_bucket = candidate_bucket - EPOCH_COUNT;
      if (!oldest_bucket_valid && bucket_nonempty[candidate_bucket]) begin
        oldest_bucket = EPOCH_WIDTH'(candidate_bucket);
        oldest_bucket_valid = 1'b1;
      end
    end

    grant = '0;
    source_found = 1'b0;
    if (advance && oldest_bucket_valid) begin
      for (source_offset = 0; source_offset < NUM_SOURCES;
           source_offset = source_offset + 1) begin
        source_index = integer'(tie_start) + source_offset;
        if (source_index >= NUM_SOURCES)
          source_index = source_index - NUM_SOURCES;
        if (!source_found && request[source_index] &&
            ((tracked[source_index] &&
              (tag[source_index] == oldest_bucket)) ||
             (!tracked[source_index] &&
              (current_epoch == oldest_bucket)))) begin
          grant[source_index] = 1'b1;
          source_found = 1'b1;
        end
      end
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      tracked <= '0;
      current_epoch <= '0;
      bucket_phase <= '0;
      tie_start <= '0;
      for (sequential_source = 0; sequential_source < NUM_SOURCES;
           sequential_source = sequential_source + 1)
        tag[sequential_source] <= '0;
    end else begin
      if (BUCKET_CYCLES == 1) begin
        current_epoch <= current_epoch + 1'b1;
        bucket_phase <= '0;
      end else if (bucket_phase == PHASE_WIDTH'(BUCKET_CYCLES - 1)) begin
        current_epoch <= current_epoch + 1'b1;
        bucket_phase <= '0;
      end else begin
        bucket_phase <= bucket_phase + 1'b1;
      end

      for (sequential_source = 0; sequential_source < NUM_SOURCES;
           sequential_source = sequential_source + 1) begin
        if (!request[sequential_source] || grant[sequential_source]) begin
          tracked[sequential_source] <= 1'b0;
        end else if (!tracked[sequential_source]) begin
          tracked[sequential_source] <= 1'b1;
          tag[sequential_source] <= current_epoch;
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
  assign epoch_debug = current_epoch;
endmodule
