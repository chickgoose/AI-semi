`timescale 1ns/1ps

`ifndef A7_WEIGHTED_FOVEA_MODULE
  `define A7_WEIGHTED_FOVEA_MODULE aer_tx16_trad_rowcol_fovea
`endif

// W6 composes the canonical N16 scalar fovea with the existing phase-related
// R1 DDR endpoint.  The fovea owns all weighted arbitration.  This shell adds
// no request queue, retry history, duplicate suppression, or output buffering.
module a7_weighted_fovea_ddr (
  input  logic        ref_clk_i,
  input  logic        sample_clk_i,
  input  logic        rst_n,
  input  logic [15:0] source_valid,
  output logic [15:0] source_ready,
  output logic        burst_clk_o,
  output logic [1:0]  burst_data_o,
  output logic [3:0]  retire_addr_o,
  output logic        retire_valid_o,
  output logic        drain_idle_o,
  output logic        protocol_fault_o
);
  logic        fovea_rst;
  logic [15:0] fovea_req;
  logic        fovea_valid;
  logic [3:0]  fovea_addr;
  logic [15:0] current_result_mask;
  logic        endpoint_valid;
  logic        endpoint_ready;
  logic        endpoint_drain_idle;

  assign fovea_rst = ~rst_n;

  // Current-result masking is purely combinational.  A registered scalar
  // result removes exactly its address from req before the next macro edge,
  // while every other live request remains under canonical fovea arbitration.
  always_comb begin
    current_result_mask = '0;
    if (fovea_valid && !$isunknown(fovea_addr))
      current_result_mask[fovea_addr] = 1'b1;
  end

  // endpoint_ready is the existing R1 safe-release qualifier.  Its reset
  // value is zero, so the first release edge naturally presents req=0 to the
  // synchronous fovea without charging wrapper state.
  assign fovea_req = endpoint_ready
                   ? (source_valid & ~current_result_mask) : '0;
  assign endpoint_valid = rst_n & fovea_valid;
  assign source_ready = (endpoint_valid & endpoint_ready)
                      ? (current_result_mask & source_valid) : '0;

  // A raw result with no live source is a combinational current fault.  It is
  // also sent through the DDR endpoint, so the final scoreboard sees a phantom
  // retirement rather than having ACK qualification silently hide it.
  always_comb begin
    protocol_fault_o = 1'b0;
    if (rst_n && fovea_valid) begin
      if ($isunknown(fovea_addr))
        protocol_fault_o = 1'b1;
      else if (!source_valid[fovea_addr])
        protocol_fault_o = 1'b1;
    end
  end

  `A7_WEIGHTED_FOVEA_MODULE canonical_fovea (
    .clk   (ref_clk_i),
    .rst   (fovea_rst),
    .req   (fovea_req),
    .valid (fovea_valid),
    .addr  (fovea_addr)
  );

  a7_r1_candidate_endpoint endpoint (
    .ref_clk_i       (ref_clk_i),
    .sample_clk_i    (sample_clk_i),
    .rst_n           (rst_n),
    .event_valid_i   (endpoint_valid),
    .event_addr_i    (fovea_addr),
    .event_ready_o   (endpoint_ready),
    .burst_clk_o     (burst_clk_o),
    .burst_data_o    (burst_data_o),
    .retire_addr_o   (retire_addr_o),
    .retire_valid_o  (retire_valid_o),
    .drain_idle_o    (endpoint_drain_idle)
  );

  // Full-composition idle excludes a live source, macro request/result,
  // same-cycle acknowledgement, endpoint work, registered retirement, or a
  // current protocol fault.  Reset is never reported as drain.
  assign drain_idle_o = rst_n & endpoint_ready & endpoint_drain_idle &
                        ~(|source_valid) & ~(|fovea_req) & ~fovea_valid &
                        ~(|source_ready) & ~retire_valid_o &
                        ~protocol_fault_o;
endmodule

`undef A7_WEIGHTED_FOVEA_MODULE
