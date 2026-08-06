// Testbench-only smoke candidate.  This is not a proposed competition design.
// It provides one stable normalized completion lane so that the clean benchmark
// can validate its own source and scoreboard behavior without inheriting the
// legacy combinational mock's output-stall violation.
module aer_clean_mock_candidate #(
  parameter int NUM_SOURCES  = 4,
  parameter int ADDR_WIDTH   = 16,
  parameter int RETIRE_LANES = 2,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if.candidate bench);
  logic output_valid;
  logic [ADDR_WIDTH-1:0] output_event;
  logic [SOURCE_WIDTH-1:0] output_source;
  logic [SOURCE_WIDTH-1:0] rr_start;
  logic slot_available;
  integer offset;
  integer candidate_source;
  integer selected_source;
  integer lane;

  always_comb begin
    slot_available = !output_valid || bench.retire_ready[0];
    selected_source = -1;
    for (offset = 0; offset < NUM_SOURCES; offset = offset + 1) begin
      candidate_source = rr_start + offset;
      if (candidate_source >= NUM_SOURCES)
        candidate_source = candidate_source - NUM_SOURCES;
      if ((selected_source < 0) && bench.source_valid[candidate_source])
        selected_source = candidate_source;
    end

    bench.source_ready = '0;
    if (slot_available && (selected_source >= 0))
      bench.source_ready[selected_source] = 1'b1;

    bench.retire_valid = '0;
    for (lane = 0; lane < RETIRE_LANES; lane = lane + 1) begin
      bench.retire_event[lane] = '0;
      bench.retire_source[lane] = '0;
    end
    bench.retire_valid[0] = output_valid;
    bench.retire_event[0] = output_event;
    bench.retire_source[0] = output_source;
  end

  always_ff @(posedge bench.clk or negedge bench.rst_n) begin
    if (!bench.rst_n) begin
      output_valid <= 1'b0;
      output_event <= '0;
      output_source <= '0;
      rr_start <= '0;
    end else if (slot_available) begin
      if (selected_source >= 0) begin
        output_valid <= 1'b1;
        output_event <= bench.source_event[selected_source];
        output_source <= SOURCE_WIDTH'(selected_source);
        if (selected_source == NUM_SOURCES-1)
          rr_start <= '0;
        else
          rr_start <= SOURCE_WIDTH'(selected_source + 1);
      end else begin
        output_valid <= 1'b0;
      end
    end
  end
endmodule
