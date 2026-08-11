`timescale 1ns/1ps

// Restricted synchronous boundary for the A7 W4 N16 link.
//
// This is intentionally not an asynchronous toggle synchronizer. The contract
// requires R=1, at most one RX retirement between core edges, and a statically
// timed phase relationship in which retire_addr_i and retire_toggle_i settle at
// least the required setup time before core_clk_i rises.
module a6_w5_phase_related_rx_boundary #(
  parameter int ADDR_WIDTH = 4
) (
  input  logic                  core_clk_i,
  input  logic                  core_reset_i,
  input  logic [ADDR_WIDTH-1:0] retire_addr_i,
  input  logic                  retire_toggle_i,
  output logic [ADDR_WIDTH-1:0] core_event_addr_o,
  output logic                  core_event_valid_o
);
  logic seen_toggle_q;

  // core_reset_i is synchronous by construction. The integration reset
  // sequence must first drain/reset A7 and hold its retirement toggle at zero.
  always_ff @(posedge core_clk_i) begin
    if (core_reset_i) begin
      seen_toggle_q      <= 1'b0;
      core_event_addr_o  <= '0;
      core_event_valid_o <= 1'b0;
    end else begin
      core_event_valid_o <= 1'b0;
      if (retire_toggle_i != seen_toggle_q) begin
        seen_toggle_q      <= retire_toggle_i;
        core_event_addr_o  <= retire_addr_i;
        core_event_valid_o <= 1'b1;
      end
    end
  end

  initial begin
    if (ADDR_WIDTH != 4)
      $fatal(1, "A6 W5 boundary freezes address-only N16 width at four bits");
  end
endmodule
