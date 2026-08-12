`timescale 1ns/1ps

// Scheduler-neutral admission for one atomic 0/1/2-address transaction.
// A transaction is valid only for count 1 or 2.  There is no endpoint queue:
// the source holds valid/count/addresses until ready, and the P6 link accepts
// one complete transaction per reference cycle after the charged arm edge.
module a7_p6_pair_launch (
  input  logic       ref_clk_i,
  input  logic       rst_n,
  input  logic       input_valid_i,
  input  logic [1:0] input_count_i,
  output logic       input_ready_o,
  output logic       launch_fire_o,
  output logic       input_protocol_error_o
);
  logic reset_release_armed_q;
  logic legal_transaction;

  always_ff @(posedge ref_clk_i or negedge rst_n) begin
    if (!rst_n)
`ifdef A7_P6_MUTATE_READY_DURING_ARM
      reset_release_armed_q <= 1'b1;
`else
      reset_release_armed_q <= 1'b0;
`endif
    else
      reset_release_armed_q <= 1'b1;
  end

  assign legal_transaction = (input_count_i == 2'd1) ||
                             (input_count_i == 2'd2);
  assign input_protocol_error_o =
      (input_valid_i && !legal_transaction) ||
      (!input_valid_i && (input_count_i != 2'd0));

`ifdef A7_P6_MUTATE_OVERFLOW_ACCEPT
  assign input_ready_o = rst_n & reset_release_armed_q;
`else
  assign input_ready_o = rst_n & reset_release_armed_q &
                         !input_protocol_error_o;
`endif
  assign launch_fire_o = input_valid_i & input_ready_o;
endmodule
