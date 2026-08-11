`timescale 1ns/1ps

// Phase-related synchronous observer. raw_toggle_i commits at burst fall and
// is sampled at the next ref-clock rise. This is not a 2FF CDC synchronizer.
module a7_r1_retire_observer (
  input  logic       ref_clk_i,
  input  logic       rst_n,
  input  logic [3:0] raw_addr_i,
  input  logic       raw_toggle_i,
  output logic [3:0] retire_addr_o,
  output logic       retire_valid_o,
  output logic       seen_toggle_o
);
  always_ff @(posedge ref_clk_i or negedge rst_n) begin
    if (!rst_n) begin
      seen_toggle_o <= 1'b0;
      retire_addr_o <= '0;
      retire_valid_o <= 1'b0;
    end else begin
      retire_valid_o <= raw_toggle_i ^ seen_toggle_o;
      seen_toggle_o <= raw_toggle_i;
      if (raw_toggle_i ^ seen_toggle_o)
        retire_addr_o <= raw_addr_i;
    end
  end
endmodule
