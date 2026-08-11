`timescale 1ns/1ps

module a9_w5_launch_qualifier (
  input  logic ref_clk_i,
  input  logic rst_n,
  input  logic event_valid_i,
  output logic event_ready_o,
  output logic launch_fire_o
);
  logic reset_release_armed_q;

  always_ff @(posedge ref_clk_i or negedge rst_n) begin
    if (!rst_n)
      reset_release_armed_q <= 1'b0;
    else
      reset_release_armed_q <= 1'b1;
  end

  assign event_ready_o = rst_n & reset_release_armed_q;
  assign launch_fire_o = event_valid_i & event_ready_o;
endmodule
