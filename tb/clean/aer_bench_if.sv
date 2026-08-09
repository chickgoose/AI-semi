interface aer_bench_if #(
  parameter int NUM_SOURCES  = 4,
  parameter int ADDR_WIDTH   = 16,
  parameter int RETIRE_LANES = 2,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (input logic clk);
  logic rst_n;

  // Normalized source-latch side.  In the mandatory common suite source_event
  // is exactly the firing source address.  Polarity/type require a separately
  // declared optional capability and are never arbitrary payload here.
  logic [NUM_SOURCES-1:0] source_valid;
  logic [NUM_SOURCES-1:0] source_ready;
  logic [ADDR_WIDTH-1:0] source_event [NUM_SOURCES];

  // Normalized completed logical events.  A packed or multi-lane candidate can
  // retire more than one event per cycle after its required decoder.
  logic [RETIRE_LANES-1:0] retire_valid;
  logic [RETIRE_LANES-1:0] retire_ready;
  logic [ADDR_WIDTH-1:0] retire_event [RETIRE_LANES];
  // Normalizer-provided source-latch identity.  This is scoreboard sideband,
  // not an extra arbitrary event payload field on the physical AER link.
  logic [SOURCE_WIDTH-1:0] retire_source [RETIRE_LANES];

  modport candidate (
    input clk, rst_n, source_valid, source_event, retire_ready,
    output source_ready, retire_valid, retire_event, retire_source
  );

  modport bench (
    input clk, source_ready, retire_valid, retire_event, retire_source,
    output rst_n, source_valid, source_event, retire_ready
  );

  modport monitor (
    input clk, rst_n, source_valid, source_ready, source_event,
          retire_valid, retire_ready, retire_event, retire_source
  );
endinterface
