`timescale 1ns/1ps

// Test-only behavioral adapters.  These are not library cells and must never
// appear in an ASIC synthesis filelist.  Without the explicit test macro this
// file defines no adapter modules, so it cannot satisfy a production closure.
`ifdef A9_W5_TEST_ONLY
module a9_w5_asic_icg_cell_adapter (
  input logic clock_i,
  input logic enable_i,
  input logic rst_n,
  output logic clock_o
);
  logic enable_latched_q;
  always_latch begin
    if (!rst_n)
      enable_latched_q = 1'b0;
    else if (!clock_i)
      enable_latched_q = enable_i;
  end
  assign clock_o = clock_i & enable_latched_q & rst_n;
endmodule

module a9_w5_asic_oddr2_cell_adapter (
  input logic clock_i,
  input logic rst_n,
  input logic [1:0] d_rise_i,
  input logic [1:0] d_fall_i,
  output logic [1:0] q_o
);
  always @(posedge clock_i or negedge rst_n)
    if (!rst_n) q_o <= '0; else q_o <= d_rise_i;
  always @(negedge clock_i or negedge rst_n)
    if (!rst_n) q_o <= '0; else q_o <= d_fall_i;
endmodule

module a9_w5_asic_iddr2_cell_adapter (
  input logic clock_i,
  input logic rst_n,
  input logic [1:0] d_i,
  output logic [1:0] q_rise_o,
  output logic [1:0] q_fall_o
);
  always @(posedge clock_i or negedge rst_n)
    if (!rst_n) q_rise_o <= '0; else q_rise_o <= d_i;
  always @(negedge clock_i or negedge rst_n)
    if (!rst_n) q_fall_o <= '0; else q_fall_o <= d_i;
endmodule
`endif
