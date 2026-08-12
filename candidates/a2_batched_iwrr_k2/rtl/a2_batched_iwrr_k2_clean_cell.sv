`timescale 1ns/1ps

// Candidate-private replacement for the common TB compatibility cell.  The
// isolated benchmark filelist intentionally omits the team-owned legacy cell.
module aer_legacy_candidate_adapter #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int RETIRE_LANES = 2,
  parameter int FIFO_DEPTH = 4,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if bench);
  a2_batched_iwrr_k2_binding #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .RETIRE_LANES(RETIRE_LANES),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) candidate(bench);

  // FIFO_DEPTH belongs to the replaceable legacy signature.  The charged K2
  // link has exactly two entries and deliberately ignores this knob.
endmodule
