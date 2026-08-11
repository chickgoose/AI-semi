`ifndef A9_W5_TECH_SELECT_SVH
`define A9_W5_TECH_SELECT_SVH

// Exactly one selection is mandatory and there is no generic fallback branch.
// An invalid selection instantiates an undefined sentinel, which the executed
// local Icarus gates reject.  Genus/Vivado may preserve unknown modules as
// black boxes unless their target flows make that condition fatal; W5 has not
// executed or proven those external-tool policies.
`ifdef A9_W5_TECH_GENERIC
  `ifdef A9_W5_TECH_ASIC
    `define A9_W5_TECH_SELECTION_ERROR
  `endif
  `ifdef A9_W5_TECH_XILINX_7SERIES
    `define A9_W5_TECH_SELECTION_ERROR
  `endif
  `define A9_W5_TECH_SELECTION_PRESENT
`endif

`ifdef A9_W5_TECH_ASIC
  `ifdef A9_W5_TECH_XILINX_7SERIES
    `define A9_W5_TECH_SELECTION_ERROR
  `endif
  `define A9_W5_TECH_SELECTION_PRESENT
`endif

`ifdef A9_W5_TECH_XILINX_7SERIES
  `define A9_W5_TECH_SELECTION_PRESENT
`endif

`ifndef A9_W5_TECH_SELECTION_PRESENT
  `define A9_W5_TECH_SELECTION_ERROR
`endif

`endif
