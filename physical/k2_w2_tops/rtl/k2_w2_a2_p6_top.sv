`timescale 1ns/1ps

// W2 physical wrapper for the complete A2 K2 scheduler plus P6 pair link.
// link_enable_i is tied high because W2 measures an always-enabled full link;
// the tie is a mode constant, not wrapper state or protocol conversion.
module k2_w2_a2_p6_top (
  input  logic        ref_clk_i,
  input  logic        sample_clk_i,
  input  logic        rst_n,
  input  logic [15:0] load_i,
  output logic        grant_commit_o,
  output logic [1:0]  grant_count_o,
  output logic [3:0]  grant_addr0_o,
  output logic [3:0]  grant_addr1_o,
  output logic [15:0] grant_bitmap_o,
  output logic        p6_clk_o,
  output logic [4:0]  p6_data_o,
  output logic [1:0]  retire_valid_o,
  output logic [3:0]  retire_addr0_o,
  output logic [3:0]  retire_addr1_o,
  output logic        protocol_error_o,
  output logic        drain_idle_o
);
  a2_batched_iwrr_p6_top composition (
    .ref_clk_i,
    .sample_clk_i,
    .rst_n,
    .link_enable_i(1'b1),
    .req_i(load_i),
    .grant_commit_o,
    .grant_count_o,
    .grant_addr0_o,
    .grant_addr1_o,
    .grant_bitmap_o,
    .p6_clk_o,
    .p6_data_o,
    .retire_valid_o,
    .retire_addr0_o,
    .retire_addr1_o,
    .protocol_error_o,
    .drain_idle_o
  );
endmodule
