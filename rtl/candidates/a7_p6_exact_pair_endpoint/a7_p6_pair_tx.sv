`timescale 1ns/1ps

module a7_p6_pair_tx (
  input  logic       ref_clk_i,
  input  logic       sample_clk_i,
  input  logic       rst_n,
  input  logic       launch_fire_i,
  input  logic [1:0] input_count_i,
  input  logic [3:0] input_addr0_i,
  input  logic [3:0] input_addr1_i,
  output logic       frame_active_o,
  output logic       p6_clk_o,
  output logic [4:0] p6_data_o
);
  logic [9:0] frame_word_q;

  // Frozen code word:
  //   bit 9     = pair flag
  //   bit 8     = reserved zero
  //   bits 7:4  = first address
  //   bits 3:0  = second address for a pair, zero for a singleton
  // Lane/address ordering is therefore explicit and lossless.
  always_ff @(posedge ref_clk_i or negedge rst_n) begin
    if (!rst_n) begin
      frame_word_q <= '0;
      frame_active_o <= 1'b0;
    end else begin
      frame_active_o <= launch_fire_i;
      if (launch_fire_i) begin
        frame_word_q[9]   <= (input_count_i == 2'd2);
        frame_word_q[8]   <= 1'b0;
        frame_word_q[7:4] <= input_addr0_i;
        frame_word_q[3:0] <= (input_count_i == 2'd2) ?
                             input_addr1_i : 4'd0;
      end
    end
  end

  // The declared phase contract places sample-clock rise in the high half of
  // ref_clk and sample-clock fall in its low half.  A characterized ICG and
  // DDR I/O mapping are required for physical use; this is the digital model.
  assign p6_data_o = ref_clk_i ? frame_word_q[4:0] : frame_word_q[9:5];
  assign p6_clk_o = sample_clk_i & frame_active_o & rst_n;
endmodule
