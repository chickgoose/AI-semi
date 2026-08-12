`timescale 1ns/1ps

// Phase-related ref-domain observer.  The two retire lanes are one atomic
// record: lane 0 is always the first address and lane 1 the second address.
module a7_p6_pair_observer (
  input  logic       ref_clk_i,
  input  logic       rst_n,
  input  logic [1:0] raw_count_i,
  input  logic [3:0] raw_addr0_i,
  input  logic [3:0] raw_addr1_i,
  input  logic       raw_toggle_i,
  input  logic       raw_protocol_error_i,
  output logic [1:0] retire_valid_o,
  output logic [3:0] retire_addr0_o,
  output logic [3:0] retire_addr1_o,
  output logic       retire_protocol_error_o,
  output logic       seen_toggle_o
);
  logic new_record;

  assign new_record = raw_toggle_i ^ seen_toggle_o;

  always_ff @(posedge ref_clk_i or negedge rst_n) begin
    if (!rst_n) begin
      retire_valid_o <= '0;
      retire_addr0_o <= '0;
      retire_addr1_o <= '0;
      retire_protocol_error_o <= 1'b0;
      seen_toggle_o <= 1'b0;
    end else begin
      retire_valid_o <= '0;
      retire_protocol_error_o <= 1'b0;
      if (new_record) begin
        retire_valid_o <= (raw_count_i == 2'd2) ? 2'b11 : 2'b01;
        retire_addr0_o <= raw_addr0_i;
        retire_addr1_o <= raw_addr1_i;
        retire_protocol_error_o <= raw_protocol_error_i ||
                                   ((raw_count_i != 2'd1) &&
                                    (raw_count_i != 2'd2));
      end
      seen_toggle_o <= raw_toggle_i;
    end
  end
endmodule
