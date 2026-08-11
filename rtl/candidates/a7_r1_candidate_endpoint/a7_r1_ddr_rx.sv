`timescale 1ns/1ps

module a7_r1_ddr_rx (
  input  logic       rst_n,
  input  logic       burst_clk_i,
  input  logic [1:0] burst_data_i,
  output logic [3:0] retire_addr_o,
  output logic       retire_toggle_o
);
  logic [1:0] low_symbol_q;

  always_ff @(posedge burst_clk_i or negedge rst_n) begin
    if (!rst_n)
      low_symbol_q <= '0;
    else
      low_symbol_q <= burst_data_i;
  end

  always_ff @(negedge burst_clk_i or negedge rst_n) begin
    if (!rst_n) begin
      retire_addr_o <= '0;
      retire_toggle_o <= 1'b0;
    end else begin
      retire_addr_o <= {burst_data_i, low_symbol_q};
      retire_toggle_o <= ~retire_toggle_o;
    end
  end
endmodule
