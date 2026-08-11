`timescale 1ns/1ps

module a7_ddr_burst_rx #(
  parameter int ADDR_WIDTH = 4,
  parameter int DATA_WIDTH = 2
) (
  input  logic                  rst_n,
  input  logic                  burst_clk_i,
  input  logic [DATA_WIDTH-1:0] burst_data_i,
  output logic [ADDR_WIDTH-1:0] retire_addr_o,
  output logic                  retire_toggle_o
);
  logic [DATA_WIDTH-1:0] low_symbol_q;

  // The rising edge opens one fixed-format frame and captures address[1:0].
  always_ff @(posedge burst_clk_i or negedge rst_n) begin
    if (!rst_n)
      low_symbol_q <= '0;
    else
      low_symbol_q <= burst_data_i;
  end

  // The falling edge closes that frame, captures address[3:2], and toggles
  // the native retirement marker exactly once. No valid-level can stick high
  // when the source-synchronous clock stops in idle.
  always_ff @(negedge burst_clk_i or negedge rst_n) begin
    if (!rst_n) begin
      retire_addr_o <= '0;
      retire_toggle_o <= 1'b0;
    end else begin
      retire_addr_o <= {burst_data_i, low_symbol_q};
      retire_toggle_o <= ~retire_toggle_o;
    end
  end

  initial begin
    if (ADDR_WIDTH != 4)
      $fatal(1, "A7 DDR burst link freezes N16 address width at four bits");
    if (DATA_WIDTH != 2)
      $fatal(1, "A7 DDR burst link requires exactly two data wires");
  end
endmodule
