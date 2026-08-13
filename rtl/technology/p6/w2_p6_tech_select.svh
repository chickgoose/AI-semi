`ifndef W2_P6_TECH_SELECT_SVH
`define W2_P6_TECH_SELECT_SVH

// Exactly one implementation is required.  There is deliberately no implicit
// default because silently changing a cell build into the RTL model is unsafe.
`ifdef W2_P6_TECH_GENERIC
  `ifdef W2_P6_TECH_GSCLIB045
    `define W2_P6_TECH_SELECTION_ERROR
  `endif
`else
  `ifndef W2_P6_TECH_GSCLIB045
    `define W2_P6_TECH_SELECTION_ERROR
  `endif
`endif

`endif
