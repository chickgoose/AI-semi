interface aer_if #(
  parameter int NUM_SOURCES = 4,
  parameter int ADDR_WIDTH  = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (input logic clk);
  logic rst_n;
  logic [NUM_SOURCES-1:0] in_valid;
  logic [NUM_SOURCES-1:0] in_ready;
  logic [ADDR_WIDTH-1:0] in_addr [NUM_SOURCES];
  logic out_valid;
  logic out_ready;
  logic [ADDR_WIDTH-1:0] out_addr;
  logic [SOURCE_WIDTH-1:0] out_src;

  modport dut (
    input  clk, rst_n, in_valid, in_addr, out_ready,
    output in_ready, out_valid, out_addr, out_src
  );
  modport tb (
    input  clk, in_ready, out_valid, out_addr, out_src,
    output rst_n, in_valid, in_addr, out_ready
  );
  modport monitor (
    input clk, rst_n, in_valid, in_ready, in_addr,
          out_valid, out_ready, out_addr, out_src
  );
endinterface
