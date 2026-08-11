`ifndef A9_W5_TECH_SELECT_SVH
`define A9_W5_TECH_SELECT_SVH

// Exactly one selection is mandatory.  The wrappers instantiate an undefined
// fail-closed sentinel when this check fails, so elaboration cannot silently
// fall back to generic logic.
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
