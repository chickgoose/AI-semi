`timescale 1ns/1ps

module w2_fovea_r1_physical_staging_top (
  input  logic        ref_clk_i,
  input  logic        sample_clk_i,
  input  logic        rst_n,
  input  logic [15:0] source_pending_i,
  output logic [15:0] source_accept_o,
  output logic        link_clk_o,
  output logic [1:0]  link_data_o,
  output logic [1:0]  retire_valid_o,
  output logic [3:0]  retire_addr0_o,
  output logic [3:0]  retire_addr1_o,
  output logic        drain_idle_o,
  output logic        protocol_error_o
);
  logic fovea_valid;
  logic [3:0] fovea_addr;
  logic [15:0] current_result_mask;
  logic [15:0] fovea_req;
  logic endpoint_valid, endpoint_ready, endpoint_drain_idle;

  always_comb begin
    current_result_mask = 16'h0000;
    if (fovea_valid) begin
      case (fovea_addr)
        4'h0: current_result_mask = 16'h0001;
        4'h1: current_result_mask = 16'h0002;
        4'h2: current_result_mask = 16'h0004;
        4'h3: current_result_mask = 16'h0008;
        4'h4: current_result_mask = 16'h0010;
        4'h5: current_result_mask = 16'h0020;
        4'h6: current_result_mask = 16'h0040;
        4'h7: current_result_mask = 16'h0080;
        4'h8: current_result_mask = 16'h0100;
        4'h9: current_result_mask = 16'h0200;
        4'ha: current_result_mask = 16'h0400;
        4'hb: current_result_mask = 16'h0800;
        4'hc: current_result_mask = 16'h1000;
        4'hd: current_result_mask = 16'h2000;
        4'he: current_result_mask = 16'h4000;
        4'hf: current_result_mask = 16'h8000;
        default: current_result_mask = 16'h0000;
      endcase
    end
  end

  logic endpoint_retire_valid;
  logic [3:0] endpoint_retire_addr;

  assign fovea_req = endpoint_ready ?
                      (source_pending_i & ~current_result_mask) : '0;
  assign endpoint_valid = rst_n & fovea_valid;
  assign source_accept_o = (endpoint_valid && endpoint_ready)
                         ? (current_result_mask & source_pending_i) : '0;
  assign protocol_error_o = rst_n && fovea_valid &&
                            !(|(current_result_mask & source_pending_i));
  assign retire_valid_o = {1'b0, endpoint_retire_valid};
  assign retire_addr0_o = endpoint_retire_addr;
  assign retire_addr1_o = 4'd0;

  aer_tx16_trad_rowcol_fovea scheduler (
    .clk(ref_clk_i), .rst(!rst_n), .req(fovea_req),
    .valid(fovea_valid), .addr(fovea_addr)
  );
  (* keep_hierarchy = "yes", dont_touch = "true",
     w2_endpoint_root = "r1" *)
  w2_r1_candidate_endpoint_tech w2_endpoint_link__r1 (
    .ref_clk_i, .sample_clk_i, .rst_n,
    .event_valid_i(endpoint_valid), .event_addr_i(fovea_addr),
    .event_ready_o(endpoint_ready), .burst_clk_o(link_clk_o),
    .burst_data_o(link_data_o), .retire_addr_o(endpoint_retire_addr),
    .retire_valid_o(endpoint_retire_valid), .drain_idle_o(endpoint_drain_idle)
  );

  assign drain_idle_o = rst_n && !(|source_pending_i) && endpoint_ready &&
                        endpoint_drain_idle && !(|fovea_req) && !fovea_valid &&
                        !(|source_accept_o) && !(|retire_valid_o) &&
                        !protocol_error_o;
endmodule
