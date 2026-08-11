`timescale 1ns/1ps
`include "a9_w5_tech_select.svh"

module a9_w5_clock_gate (
  input  logic clock_i,
  input  logic enable_i,
  input  logic rst_n,
  output logic clock_o
);
`ifdef A9_W5_TECH_SELECTION_ERROR
  a9_w5_invalid_or_missing_technology_selection__compile_error invalid_selection();
  assign clock_o = 1'bx;
`elsif A9_W5_TECH_GENERIC
  logic enable_latched_q;

  always_latch begin
    if (!rst_n)
      enable_latched_q = 1'b0;
    else if (!clock_i)
      enable_latched_q = enable_i;
  end

  assign clock_o = clock_i & enable_latched_q & rst_n;
`elsif A9_W5_TECH_ASIC
  // The target-owned adapter must be supplied explicitly.  No black-box stub
  // is shipped because an unresolved cell must fail elaboration.
  a9_w5_asic_icg_cell_adapter icg (
    .clock_i(clock_i),
    .enable_i(enable_i),
    .rst_n(rst_n),
    .clock_o(clock_o)
  );
`elsif A9_W5_TECH_XILINX_7SERIES
  // BUFGCE is the explicit Vivado clock-control boundary.  CE must meet the
  // selected device's BUFGCE timing checks; the A7 contract changes enable
  // while sample clock is low.  Reset is legal only drained and clock-low.
  BUFGCE bufgce (
    .I(clock_i),
    .CE(enable_i & rst_n),
    .O(clock_o)
  );
`endif
endmodule
