interface aer_bench_if #(
  parameter int NUM_SOURCES  = 4,
  parameter int ADDR_WIDTH   = 16,
  parameter int RETIRE_LANES = 2
) (input logic clk);
  logic rst_n;

  // Normalized source-latch side.  event_addr is the event identity
  // (coordinate plus optional type), not an arbitrary payload.
  logic [NUM_SOURCES-1:0] source_valid;
  logic [NUM_SOURCES-1:0] source_ready;
  logic [ADDR_WIDTH-1:0] source_event [NUM_SOURCES];

  // Normalized completed logical events.  A packed or multi-lane candidate can
  // retire more than one event per cycle after its required decoder.
  logic [RETIRE_LANES-1:0] retire_valid;
  logic [RETIRE_LANES-1:0] retire_ready;
  logic [ADDR_WIDTH-1:0] retire_event [RETIRE_LANES];

  modport candidate (
    input clk, rst_n, source_valid, source_event, retire_ready,
    output source_ready, retire_valid, retire_event
  );

  modport bench (
    input clk, source_ready, retire_valid, retire_event,
    output rst_n, source_valid, source_event, retire_ready
  );

  modport monitor (
    input clk, rst_n, source_valid, source_ready, source_event,
          retire_valid, retire_ready, retire_event
  );
endinterface
