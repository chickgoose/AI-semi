// A3 W5 audit-only same-top selector around the pinned production W5 tops.
// It adds no state or functional behavior; link data is padded only at the
// observation port so both STYLE values have the same synthesis top ports.
module a3_w5_r1_endpoint_top #(
  parameter int STYLE = 0
) (
  input  logic       ref_clk_i,
  input  logic       sample_clk_i,
  input  logic       rst_n,
  input  logic       event_valid_i,
  input  logic [3:0] event_addr_i,
  output logic       event_ready_o,
  output logic       link_clk_o,
  output logic [3:0] link_data_observe_o,
  output logic [3:0] retire_addr_o,
  output logic       retire_valid_o,
  output logic       drain_idle_o
);
  generate
    if (STYLE == 0) begin : parallel4
      a7_r1_parallel_reference_top endpoint (
        .ref_clk_i(ref_clk_i), .sample_clk_i(sample_clk_i), .rst_n(rst_n),
        .event_valid_i(event_valid_i), .event_addr_i(event_addr_i),
        .event_ready_o(event_ready_o), .link_strobe_o(link_clk_o),
        .link_data_o(link_data_observe_o), .retire_addr_o(retire_addr_o),
        .retire_valid_o(retire_valid_o), .drain_idle_o(drain_idle_o));
    end else if (STYLE == 1) begin : ddr2
      logic [1:0] data;
      a7_r1_candidate_endpoint endpoint (
        .ref_clk_i(ref_clk_i), .sample_clk_i(sample_clk_i), .rst_n(rst_n),
        .event_valid_i(event_valid_i), .event_addr_i(event_addr_i),
        .event_ready_o(event_ready_o), .burst_clk_o(link_clk_o),
        .burst_data_o(data), .retire_addr_o(retire_addr_o),
        .retire_valid_o(retire_valid_o), .drain_idle_o(drain_idle_o));
      assign link_data_observe_o = {2'b00, data};
    end
  endgenerate
endmodule
