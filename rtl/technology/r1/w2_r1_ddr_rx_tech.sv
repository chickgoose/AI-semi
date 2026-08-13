`timescale 1ns/1ps

module w2_r1_ddr_rx_tech (
  input  logic       rst_n,
  input  logic       burst_clk_i,
  input  logic [1:0] burst_data_i,
  output logic [3:0] retire_addr_o,
  output logic       retire_toggle_o
);
  logic [1:0] low_symbol_q;
  logic [4:0] closing_state_d;
  logic [4:0] closing_state_q;

  w2_p6_posedge_capture #(.WIDTH(2)) low_symbol_capture (
    .clock_i(burst_clk_i), .rst_ni(rst_n),
    .data_i(burst_data_i), .data_o(low_symbol_q)
  );

  assign closing_state_d = {burst_data_i, low_symbol_q,
                            ~closing_state_q[0]};
  w2_p6_negedge_capture #(.WIDTH(5)) closing_capture (
    .clock_i(burst_clk_i), .rst_ni(rst_n),
    .data_i(closing_state_d), .data_o(closing_state_q)
  );
  assign retire_addr_o = closing_state_q[4:1];
  assign retire_toggle_o = closing_state_q[0];
endmodule
