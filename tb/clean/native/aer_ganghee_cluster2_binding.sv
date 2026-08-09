// TB-only binding for Ganghee cluster2's native level-request protocol.
// It contains no storage, arbitration, queue, sink backpressure compensation,
// or metadata reconstruction.  Raw native output identity is always exposed.
`timescale 1ns/1ps

`ifdef AER_CLEAN_GANGHEE_CLUSTER2
module aer_ganghee_cluster2_binding #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int RETIRE_LANES = 8,
  localparam int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if bench);
  // AER_CLUSTER2_BINDING_BEGIN
  logic [15:0] cluster2_req;
  logic [15:0] cluster2_current_result_mask;
  logic cluster2_valid0;
  logic [1:0] cluster2_row0;
  logic [3:0] cluster2_col_mask0;
  logic cluster2_valid1;
  logic [1:0] cluster2_row1;
  logic [3:0] cluster2_col_mask1;
  integer cluster2_col;
  integer cluster2_source;
  integer cluster2_lane;
  integer cluster2_mask_col;
  integer cluster2_mask_source;

  initial begin
    if ((NUM_SOURCES != 16) || (RETIRE_LANES < 8))
      $fatal(2, "cluster2 binding requires NUM_SOURCES=16 RETIRE_LANES>=8");
  end

  // Current-result acknowledgement masking prevents a registered DUT from
  // sampling the same held level request again on its completion edge.
  always_comb begin
    cluster2_current_result_mask = '0;
    for (cluster2_mask_col = 0; cluster2_mask_col < 4;
         cluster2_mask_col = cluster2_mask_col + 1) begin
      cluster2_mask_source = (integer'(cluster2_row0) * 4) + cluster2_mask_col;
      if (cluster2_valid0 && cluster2_col_mask0[cluster2_mask_col])
        cluster2_current_result_mask[cluster2_mask_source] = 1'b1;
      cluster2_mask_source = (integer'(cluster2_row1) * 4) + cluster2_mask_col;
      if (cluster2_valid1 && cluster2_col_mask1[cluster2_mask_col])
        cluster2_current_result_mask[cluster2_mask_source] = 1'b1;
    end
  end
  assign cluster2_req = bench.source_valid & ~cluster2_current_result_mask;

  `AER_GANGHEE_CLUSTER2_MODULE raw_cluster2_dut (
    .clk       (bench.clk),
    .rst       (~bench.rst_n),
    .req       (cluster2_req),
    .valid0    (cluster2_valid0),
    .row0      (cluster2_row0),
    .col_mask0 (cluster2_col_mask0),
    .valid1    (cluster2_valid1),
    .row1      (cluster2_row1),
    .col_mask1 (cluster2_col_mask1)
  );

  always_comb begin
    // A raw result acknowledges a source only while its level request is live.
    bench.source_ready = bench.source_valid & cluster2_current_result_mask;
    bench.retire_valid = '0;
    for (cluster2_lane = 0; cluster2_lane < RETIRE_LANES;
         cluster2_lane = cluster2_lane + 1) begin
      bench.retire_event[cluster2_lane] = '0;
      bench.retire_source[cluster2_lane] = '0;
    end

    // AER_CLUSTER2_RAW_OBSERVATION_BEGIN
    // Deliberately do not gate retirement with pending/request/mask state.  A
    // repeated raw result must reach the common scoreboard as a phantom.
    for (cluster2_col = 0; cluster2_col < 4;
         cluster2_col = cluster2_col + 1) begin
      cluster2_source = (integer'(cluster2_row0) * 4) + cluster2_col;
      if (cluster2_valid0 && cluster2_col_mask0[cluster2_col]) begin
        bench.retire_valid[cluster2_col] = 1'b1;
        bench.retire_event[cluster2_col] = ADDR_WIDTH'(cluster2_source);
        bench.retire_source[cluster2_col] = SOURCE_WIDTH'(cluster2_source);
      end
      cluster2_source = (integer'(cluster2_row1) * 4) + cluster2_col;
      if (cluster2_valid1 && cluster2_col_mask1[cluster2_col]) begin
        bench.retire_valid[4 + cluster2_col] = 1'b1;
        bench.retire_event[4 + cluster2_col] = ADDR_WIDTH'(cluster2_source);
        bench.retire_source[4 + cluster2_col] = SOURCE_WIDTH'(cluster2_source);
      end
    end
    // AER_CLUSTER2_RAW_OBSERVATION_END
  end
  // AER_CLUSTER2_BINDING_END
endmodule
`endif
