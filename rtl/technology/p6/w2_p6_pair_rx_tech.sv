`timescale 1ns/1ps

// Technology-bound form of the frozen a7_p6_pair_rx.  The positive-edge bank
// has an evidenced cell binding; the closing negative edge remains inferred.
module w2_p6_pair_rx_tech (
  input  logic       rst_n,
  input  logic       p6_clk_i,
  input  logic [4:0] p6_data_i,
  output logic [1:0] raw_count_o,
  output logic [3:0] raw_addr0_o,
  output logic [3:0] raw_addr1_o,
  output logic       raw_toggle_o,
  output logic       raw_protocol_error_o
);
  logic [4:0] low_symbol_q;
  logic [9:0] closing_word;
  logic [11:0] closing_state_d;
  logic [11:0] closing_state_q;

  w2_p6_posedge_capture #(.WIDTH(5)) low_symbol_capture (
    .clock_i(p6_clk_i),
    .rst_ni(rst_n),
    .data_i(p6_data_i),
    .data_o(low_symbol_q)
  );

  assign closing_word = {p6_data_i, low_symbol_q};
  assign closing_state_d[11:10] = closing_word[9] ? 2'd2 : 2'd1;
  assign closing_state_d[9:6] = closing_word[7:4];
  assign closing_state_d[5:2] = closing_word[9] ? closing_word[3:0] : 4'd0;
  assign closing_state_d[1] = closing_word[8] ||
                              (!closing_word[9] &&
                               (closing_word[3:0] != 4'd0));
  assign closing_state_d[0] = ~closing_state_q[0];

  w2_p6_negedge_capture #(.WIDTH(12)) closing_capture (
    .clock_i(p6_clk_i),
    .rst_ni(rst_n),
    .data_i(closing_state_d),
    .data_o(closing_state_q)
  );

  assign raw_count_o = closing_state_q[11:10];
  assign raw_addr0_o = closing_state_q[9:6];
  assign raw_addr1_o = closing_state_q[5:2];
  assign raw_protocol_error_o = closing_state_q[1];
  assign raw_toggle_o = closing_state_q[0];
endmodule
