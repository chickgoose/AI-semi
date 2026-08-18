`timescale 1ns/1ps

// Single-clock transmitter for one atomic ordered K2 record per cycle.
//
// The physical link is information-minimal for this contract: valid plus two
// four-bit addresses.  When valid is one, equal addresses encode a singleton
// and distinct addresses encode the ordered pair {addr0, addr1}.  When valid
// is zero both address fields are zero.  All state changes only on clk_i's
// rising edge; there is no forwarded/generated clock or clock gating.
module w2_single_edge_pair_tx (
  input  logic       clk_i,
  input  logic       rst_i,
  input  logic       link_enable_i,
  input  logic [1:0] input_count_i,
  input  logic [3:0] input_addr0_i,
  input  logic [3:0] input_addr1_i,
  output logic       input_ready_o,
  output logic       input_commit_o,
  output logic [1:0] policy_microsteps_o,
  output logic       protocol_error_o,
  output logic       link_valid_o,
  output logic [3:0] link_addr0_o,
  output logic [3:0] link_addr1_o
);
  logic shape_error;
  logic protocol_error_q;

  always_comb begin
    shape_error = 1'b0;
    case (input_count_i)
      2'd0: shape_error = (input_addr0_i != 4'd0) ||
                            (input_addr1_i != 4'd0);
      // Count-one has one canonical scheduler-side representation.  The TX
      // later duplicates addr0 onto the wire; a nonzero unused addr1 is an
      // upstream protocol error rather than an alternate singleton encoding.
      2'd1: shape_error = (input_addr1_i != 4'd0);
      2'd2: shape_error = (input_addr0_i == input_addr1_i);
      default: shape_error = 1'b1;
    endcase
  end

  assign input_ready_o = !rst_i && link_enable_i && !shape_error;
  assign input_commit_o = (input_count_i != 2'd0) && input_ready_o;
  assign policy_microsteps_o = input_commit_o ? input_count_i : 2'd0;
  assign protocol_error_o = protocol_error_q || shape_error;

  always_ff @(posedge clk_i) begin
    if (rst_i) begin
      protocol_error_q <= 1'b0;
      link_valid_o <= 1'b0;
      link_addr0_o <= 4'd0;
      link_addr1_o <= 4'd0;
    end else begin
      if (shape_error)
        protocol_error_q <= 1'b1;

      // The receiver is permanently able to consume one link cell per edge.
      // An idle cell therefore follows every cycle without an input commit.
      link_valid_o <= input_commit_o;
      if (input_commit_o) begin
        link_addr0_o <= input_addr0_i;
        link_addr1_o <= (input_count_i == 2'd2) ? input_addr1_i :
                                                     input_addr0_i;
      end else begin
        link_addr0_o <= 4'd0;
        link_addr1_o <= 4'd0;
      end
    end
  end
endmodule
