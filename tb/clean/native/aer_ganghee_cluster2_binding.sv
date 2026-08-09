// TB-only stateless binding for Ganghee's two-lane row/column-bitmap AER DUT.
//
// Native hardware contract (kept verbatim):
//   input  clk, rst, req[15:0]
//   output valid0, row0[1:0], col_mask0[3:0]
//   output valid1, row1[1:0], col_mask1[3:0]
//
// Each asserted column bit is one completed address event.  The binding only
// expands the two native bitmap words into eight normalized scoreboard lanes;
// it adds no storage, arbitration, grant history, or backpressure behavior.
`timescale 1ns/1ps

`ifndef AER_GANGHEE_CLUSTER2_MODULE
  `define AER_GANGHEE_CLUSTER2_MODULE aer_tx16_trad_rowcol_fovea_cluster2
`endif

module aer_ganghee_cluster2_binding #(
  parameter int NUM_SOURCES  = 16,
  parameter int ADDR_WIDTH   = 16,
  parameter int RETIRE_LANES = 8,
  parameter int FIFO_DEPTH   = 0,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if.candidate bench);
  logic        native_rst;
  logic [15:0] native_req;
  logic        native_valid0;
  logic [1:0]  native_row0;
  logic [3:0]  native_col_mask0;
  logic        native_valid1;
  logic [1:0]  native_row1;
  logic [3:0]  native_col_mask1;
  logic [15:0] native_result_mask;
  logic [15:0] native_ack_mask;
  integer mask_col;
  integer map_col;
  integer source;
  integer lane;

  assign native_rst = ~bench.rst_n;

  // Reset quiet is checked at the unmasked native boundary. A normalizer may
  // not make a stale native result disappear merely because no request lives.
  always @(negedge bench.clk) begin
    if (!bench.rst_n &&
        ((native_valid0 !== 1'b0) || (native_valid1 !== 1'b0)))
      $fatal(1, "GANGHEE_CLUSTER2_BINDING native valid active during reset lane0=%b lane1=%b",
             native_valid0, native_valid1);
  end

  always_comb begin
    native_result_mask = '0;
    if (native_valid0 && !$isunknown({native_row0, native_col_mask0}))
      for (mask_col = 0; mask_col < 4; mask_col = mask_col + 1)
        if (native_col_mask0[mask_col])
          native_result_mask[(integer'(native_row0) * 4) + mask_col] = 1'b1;
    if (native_valid1 && !$isunknown({native_row1, native_col_mask1}))
      for (mask_col = 0; mask_col < 4; mask_col = mask_col + 1)
        if (native_col_mask1[mask_col])
          native_result_mask[(integer'(native_row1) * 4) + mask_col] = 1'b1;
  end

  // Results acknowledge only source events that are still pending.  Masking
  // those requests before the next active sampling edge prevents a registered
  // native bitmap from being observed as a second completion.
  assign native_ack_mask = native_result_mask & bench.source_valid;
  assign native_req = bench.source_valid & ~native_ack_mask;

  `AER_GANGHEE_CLUSTER2_MODULE native_dut (
    .clk       (bench.clk),
    .rst       (native_rst),
    .req       (native_req),
    .valid0    (native_valid0),
    .row0      (native_row0),
    .col_mask0 (native_col_mask0),
    .valid1    (native_valid1),
    .row1      (native_row1),
    .col_mask1 (native_col_mask1)
  );

  always_comb begin
    bench.source_ready = native_ack_mask;
    bench.retire_valid = '0;
    for (lane = 0; lane < RETIRE_LANES; lane = lane + 1) begin
      bench.retire_event[lane] = '0;
      bench.retire_source[lane] = '0;
    end

    for (map_col = 0; map_col < 4; map_col = map_col + 1) begin
      source = (integer'(native_row0) * 4) + map_col;
      // Observe the raw native result even when no common source is pending.
      // The scoreboard must see a repeated/phantom result instead of having
      // the acknowledgement mask silently discard it.
      if (native_valid0 && !$isunknown({native_row0, native_col_mask0}) &&
          native_col_mask0[map_col]) begin
        bench.retire_valid[map_col] = 1'b1;
        bench.retire_event[map_col] = ADDR_WIDTH'(source);
        bench.retire_source[map_col] = SOURCE_WIDTH'(source);
      end

      source = (integer'(native_row1) * 4) + map_col;
      if (native_valid1 && !$isunknown({native_row1, native_col_mask1}) &&
          native_col_mask1[map_col]) begin
        bench.retire_valid[4 + map_col] = 1'b1;
        bench.retire_event[4 + map_col] = ADDR_WIDTH'(source);
        bench.retire_source[4 + map_col] = SOURCE_WIDTH'(source);
      end
    end
  end

  initial begin
    if (NUM_SOURCES != 16)
      $fatal(1, "GANGHEE_CLUSTER2_BINDING requires NUM_SOURCES=16");
    if (RETIRE_LANES != 8)
      $fatal(1, "GANGHEE_CLUSTER2_BINDING requires RETIRE_LANES=8");
    if (ADDR_WIDTH <= 0)
      $fatal(1, "GANGHEE_CLUSTER2_BINDING requires positive ADDR_WIDTH");
    if (FIFO_DEPTH != 0)
      $fatal(1, "GANGHEE_CLUSTER2_BINDING requires FIFO_DEPTH=0");
  end

  always @(posedge bench.clk) begin
    if (bench.rst_n) begin
      if ((native_result_mask != '0) && (bench.retire_ready !== '1))
        $error("GANGHEE_CLUSTER2_BINDING supports sink-always-ready only");
      if (native_valid0 && $isunknown({native_row0, native_col_mask0}))
        $error("GANGHEE_CLUSTER2_BINDING lane0 result contains unknown bits");
      if (native_valid1 && $isunknown({native_row1, native_col_mask1}))
        $error("GANGHEE_CLUSTER2_BINDING lane1 result contains unknown bits");
      if (native_valid0 && (native_col_mask0 == '0))
        $error("GANGHEE_CLUSTER2_BINDING lane0 valid with empty bitmap");
      if (native_valid1 && (native_col_mask1 == '0))
        $error("GANGHEE_CLUSTER2_BINDING lane1 valid with empty bitmap");
      if (native_valid0 && !((native_row0 == 2'd1) || (native_row0 == 2'd2)))
        $error("GANGHEE_CLUSTER2_BINDING lane0 emitted non-center row=%0d", native_row0);
      if (native_valid1 && !((native_row1 == 2'd0) || (native_row1 == 2'd3)))
        $error("GANGHEE_CLUSTER2_BINDING lane1 emitted non-peripheral row=%0d", native_row1);
      if ((native_result_mask & ~bench.source_valid) != '0)
        $fatal(1, "GANGHEE_CLUSTER2_BINDING duplicate/phantom bitmap mask=%h pending=%h",
               native_result_mask, bench.source_valid);
      if ((native_result_mask & native_req) != '0)
        $error("GANGHEE_CLUSTER2_BINDING acknowledged requests were not masked");
      if ($countones(bench.retire_valid) != $countones(native_result_mask))
        $error("GANGHEE_CLUSTER2_BINDING raw result mapping is inconsistent");
    end
  end
endmodule

`undef AER_GANGHEE_CLUSTER2_MODULE
