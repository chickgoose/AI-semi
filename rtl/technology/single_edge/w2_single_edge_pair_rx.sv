`timescale 1ns/1ps

// Shared-clock receiver for the 9-wire single-edge ordered-record encoding.
// Retirement is always-ready and is a one-cycle pulse.  A link idle symbol
// with nonzero payload is malformed and sets the sticky protocol error.
module w2_single_edge_pair_rx (
  input  logic       clk_i,
  input  logic       rst_i,
  input  logic       link_valid_i,
  input  logic [3:0] link_addr0_i,
  input  logic [3:0] link_addr1_i,
  output logic [1:0] retire_valid_o,
  output logic [3:0] retire_addr0_o,
  output logic [3:0] retire_addr1_o,
  output logic       protocol_error_o
);
  logic idle_payload_error;

  assign idle_payload_error = !link_valid_i &&
                              ((link_addr0_i != 4'd0) ||
                               (link_addr1_i != 4'd0));

  always_ff @(posedge clk_i) begin
    if (rst_i) begin
      retire_valid_o <= 2'b00;
      retire_addr0_o <= 4'd0;
      retire_addr1_o <= 4'd0;
      protocol_error_o <= 1'b0;
    end else begin
      retire_valid_o <= 2'b00;
      retire_addr0_o <= 4'd0;
      retire_addr1_o <= 4'd0;

      if (idle_payload_error)
        protocol_error_o <= 1'b1;

      if (link_valid_i) begin
        retire_valid_o <= (link_addr0_i == link_addr1_i) ? 2'b01 :
                                                               2'b11;
        retire_addr0_o <= link_addr0_i;
        retire_addr1_o <= (link_addr0_i == link_addr1_i) ? 4'd0 :
                                                               link_addr1_i;
      end
    end
  end
endmodule
