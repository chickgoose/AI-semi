`ifndef HYEONSU_NUM_SOURCES
  `define HYEONSU_NUM_SOURCES 4
`endif

`ifndef HYEONSU_ADDR_WIDTH
  `define HYEONSU_ADDR_WIDTH 16
`endif

// Compatibility package for the server testbench. It keeps Hyeonsu's
// package-level constants while also supplying the index_width API used by
// the qualified A23 RTL. The production rtl/common/aer_pkg.sv is deliberately
// not compiled in this harness, avoiding two packages with the same name.
package aer_pkg;
  function automatic int unsigned index_width(input int unsigned item_count);
    if (item_count <= 1) return 1;
    return $clog2(item_count);
  endfunction

  parameter int unsigned NUM_SOURCES = `HYEONSU_NUM_SOURCES;
  parameter int unsigned ADDR_WIDTH = `HYEONSU_ADDR_WIDTH;
  parameter int unsigned SRC_WIDTH = index_width(NUM_SOURCES);
  parameter int unsigned DEFAULT_NUM_SOURCES = NUM_SOURCES;
endpackage
