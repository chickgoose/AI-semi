`timescale 1ns/1ps

// Equal-boundary structural shells.  The canonical fovea is intentionally
// unchanged: it has no ready input, so endpoint_ready_o is observable evidence
// rather than backpressure applied to the transmitter.
module a5_fovea_a7_ddr_top (
  input  logic        ref_clk_i,
  input  logic        sample_clk_i,
  input  logic        rst_n,
  input  logic [15:0] req_i,
  output logic        endpoint_ready_o,
  output logic        burst_clk_o,
  output logic [1:0]  burst_data_o,
  output logic [3:0]  retire_addr_o,
  output logic        retire_valid_o,
  output logic        drain_idle_o
);
  logic fovea_valid;
  logic [3:0] fovea_addr;

  aer_tx16_trad_rowcol_fovea fovea (
    .clk(ref_clk_i), .rst(~rst_n), .req(req_i),
    .valid(fovea_valid), .addr(fovea_addr)
  );
  a7_r1_candidate_endpoint endpoint (
    .ref_clk_i(ref_clk_i), .sample_clk_i(sample_clk_i), .rst_n(rst_n),
    .event_valid_i(fovea_valid), .event_addr_i(fovea_addr),
    .event_ready_o(endpoint_ready_o), .burst_clk_o(burst_clk_o),
    .burst_data_o(burst_data_o), .retire_addr_o(retire_addr_o),
    .retire_valid_o(retire_valid_o), .drain_idle_o(drain_idle_o)
  );
endmodule

module a5_fovea_a7_parallel_top (
  input  logic        ref_clk_i,
  input  logic        sample_clk_i,
  input  logic        rst_n,
  input  logic [15:0] req_i,
  output logic        endpoint_ready_o,
  output logic        link_strobe_o,
  output logic [3:0]  link_data_o,
  output logic [3:0]  retire_addr_o,
  output logic        retire_valid_o,
  output logic        drain_idle_o
);
  logic fovea_valid;
  logic [3:0] fovea_addr;

  aer_tx16_trad_rowcol_fovea fovea (
    .clk(ref_clk_i), .rst(~rst_n), .req(req_i),
    .valid(fovea_valid), .addr(fovea_addr)
  );
  a7_r1_parallel_reference_top endpoint (
    .ref_clk_i(ref_clk_i), .sample_clk_i(sample_clk_i), .rst_n(rst_n),
    .event_valid_i(fovea_valid), .event_addr_i(fovea_addr),
    .event_ready_o(endpoint_ready_o), .link_strobe_o(link_strobe_o),
    .link_data_o(link_data_o), .retire_addr_o(retire_addr_o),
    .retire_valid_o(retire_valid_o), .drain_idle_o(drain_idle_o)
  );
endmodule
