`timescale 1ns/1ps

module k2_ordered_link_binding (
  input  logic       clk,
  input  logic       rst,
  input  logic [1:0] offer_count,
  input  logic [3:0] offer_addr0,
  input  logic [3:0] offer_addr1,
  output logic       offer_ready,
  output logic [1:0] retire_valid,
  output logic [3:0] retire_addr0,
  output logic [3:0] retire_addr1,
  input  logic [1:0] retire_ready,
  output logic       link_empty
);
  k2_ordered_link link (.*);
endmodule
