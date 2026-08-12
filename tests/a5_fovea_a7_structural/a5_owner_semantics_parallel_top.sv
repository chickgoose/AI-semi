`timescale 1ns/1ps

// Audit-only parallel reference for the exact owner boundary in d3c52f0 and
// b520125.  The acceptance mask, protocol fault, and full-drain equations are
// intentionally identical; only the A7 endpoint/link encoding is replaced.
module a5_owner_semantics_parallel_top (
  input  logic        ref_clk_i,
  input  logic        sample_clk_i,
  input  logic        rst_n,
  input  logic [15:0] source_valid,
  output logic [15:0] source_ready,
  output logic        link_strobe_o,
  output logic [3:0]  link_data_o,
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

  always_comb begin
    current_result_mask = '0;
    if (fovea_valid && !$isunknown(fovea_addr))
      current_result_mask[fovea_addr] = 1'b1;
  end

  assign fovea_req = endpoint_ready
                   ? (source_valid & ~current_result_mask) : '0;
  assign endpoint_valid = rst_n & fovea_valid;
  assign source_ready = (endpoint_valid & endpoint_ready)
                      ? (current_result_mask & source_valid) : '0;

  always_comb begin
    protocol_fault_o = 1'b0;
    if (rst_n && fovea_valid) begin
      if ($isunknown(fovea_addr))
        protocol_fault_o = 1'b1;
      else if (!source_valid[fovea_addr])
        protocol_fault_o = 1'b1;
    end
  end

  aer_tx16_trad_rowcol_fovea canonical_fovea (
    .clk(ref_clk_i), .rst(fovea_rst), .req(fovea_req),
    .valid(fovea_valid), .addr(fovea_addr)
  );

  a7_r1_parallel_reference_top endpoint (
    .ref_clk_i(ref_clk_i), .sample_clk_i(sample_clk_i), .rst_n(rst_n),
    .event_valid_i(endpoint_valid), .event_addr_i(fovea_addr),
    .event_ready_o(endpoint_ready), .link_strobe_o(link_strobe_o),
    .link_data_o(link_data_o), .retire_addr_o(retire_addr_o),
    .retire_valid_o(retire_valid_o), .drain_idle_o(endpoint_drain_idle)
  );

  assign drain_idle_o = rst_n & endpoint_ready & endpoint_drain_idle &
                        ~(|source_valid) & ~(|fovea_req) & ~fovea_valid &
                        ~(|source_ready) & ~retire_valid_o &
                        ~protocol_fault_o;
endmodule
