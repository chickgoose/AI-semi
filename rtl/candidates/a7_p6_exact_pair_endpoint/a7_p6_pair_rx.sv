`timescale 1ns/1ps

module a7_p6_pair_rx (
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

  always_ff @(posedge p6_clk_i or negedge rst_n) begin
    if (!rst_n)
      low_symbol_q <= '0;
    else
      low_symbol_q <= p6_data_i;
  end

  assign closing_word = {p6_data_i, low_symbol_q};

  always_ff @(negedge p6_clk_i or negedge rst_n) begin
    if (!rst_n) begin
      raw_count_o <= '0;
      raw_addr0_o <= '0;
      raw_addr1_o <= '0;
`ifdef A7_P6_MUTATE_RESET_PHANTOM
      raw_toggle_o <= 1'b1;
`else
      raw_toggle_o <= 1'b0;
`endif
      raw_protocol_error_o <= 1'b0;
    end else begin
      raw_count_o <= closing_word[9] ? 2'd2 : 2'd1;
`ifdef A7_P6_MUTATE_SWAP_PAIR
      raw_addr0_o <= closing_word[9] ? closing_word[3:0] :
                                           closing_word[7:4];
      raw_addr1_o <= closing_word[9] ? closing_word[7:4] : 4'd0;
`else
      raw_addr0_o <= closing_word[7:4];
      raw_addr1_o <= closing_word[9] ? closing_word[3:0] : 4'd0;
`endif
      raw_protocol_error_o <= closing_word[8] ||
                              (!closing_word[9] &&
                               (closing_word[3:0] != 4'd0));
      raw_toggle_o <= ~raw_toggle_o;
    end
  end
endmodule
