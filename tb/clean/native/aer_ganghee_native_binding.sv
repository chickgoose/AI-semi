// TB-only binding for Ganghee's native AER protocol.
//
// Native hardware contract (kept verbatim):
//   input  clk, rst, req[15:0]
//   output valid, addr[3:0]
//
// This module is deliberately a combinational protocol normalization shell.
// It contains no event storage, FIFO, arbitration, grant history, or output
// backpressure compensation.  The native valid/address observation is an
// implicit acknowledgement of the currently pending source event.
`timescale 1ns/1ps

`ifndef AER_GANGHEE_NATIVE_MODULE
  `define AER_GANGHEE_NATIVE_MODULE ganghee_native_dut
`endif

module aer_ganghee_native_binding #(
  parameter int NUM_SOURCES  = 16,
  parameter int ADDR_WIDTH   = 16,
  parameter int RETIRE_LANES = 1,
  // Accepted only because the common TB's replaceable candidate cell carries
  // this parameter.  It does not create or configure storage in this binding.
  parameter int FIFO_DEPTH   = 0,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if.candidate bench);
  logic        native_rst;
  logic [15:0] native_req;
  logic        native_valid;
  logic [3:0]  native_addr;
  logic        native_ack;
  logic [15:0] native_ack_mask;
  integer lane;

  assign native_rst = ~bench.rst_n;

  // Mask the acknowledged source before the next active sampling edge.  This
  // is a stateless TB-driver timing adaptation; all other pending requests
  // remain visible and the native DUT still owns arbitration.
  always_comb begin
    native_ack_mask = '0;
    if (native_valid && !$isunknown(native_addr) &&
        bench.source_valid[native_addr])
      native_ack_mask[native_addr] = 1'b1;
  end
  assign native_req = bench.source_valid & ~native_ack_mask;

  `AER_GANGHEE_NATIVE_MODULE native_dut (
    .clk   (bench.clk),
    .rst   (native_rst),
    .req   (native_req),
    .valid (native_valid),
    .addr  (native_addr)
  );

  always_comb begin
    bench.source_ready = '0;
    bench.retire_valid = '0;
    for (lane = 0; lane < RETIRE_LANES; lane = lane + 1) begin
      bench.retire_event[lane] = '0;
      bench.retire_source[lane] = '0;
    end

    // A native result acknowledges only a request that is still pending.
    // Once the common scoreboard clears that pending bit, a held/repeated
    // native result cannot become a second completion.
    native_ack = |native_ack_mask;
    if (native_ack) begin
      bench.source_ready[native_addr] = 1'b1;
      bench.retire_valid[0] = 1'b1;
      // Ganghee's native link returns only source identity.  The common
      // scoreboard event identity is reconstructed from the one live pending
      // latch; it is TB sideband and is not added to the native DUT payload.
      bench.retire_event[0] = bench.source_event[native_addr];
      bench.retire_source[0] = SOURCE_WIDTH'(native_addr);
    end
  end

  initial begin
    if (NUM_SOURCES != 16)
      $fatal(1, "GANGHEE_NATIVE_BINDING requires NUM_SOURCES=16");
    if (RETIRE_LANES != 1)
      $fatal(1, "GANGHEE_NATIVE_BINDING requires RETIRE_LANES=1");
    if (ADDR_WIDTH <= 0)
      $fatal(1, "GANGHEE_NATIVE_BINDING requires positive ADDR_WIDTH");
    if (FIFO_DEPTH < 0)
      $fatal(1, "GANGHEE_NATIVE_BINDING FIFO_DEPTH is compatibility-only");
  end

  // Capability and protocol checks only; these assertions add no state to the
  // binding or DUT.  A result without a live request is a duplicate/phantom
  // native observation and must not be hidden by the normalizer.
  always @(posedge bench.clk) begin
    if (bench.rst_n) begin
      if (bench.retire_ready !== '1)
        $error("GANGHEE_NATIVE_BINDING supports sink-always-ready only");
      if (native_valid && $isunknown(native_addr))
        $error("GANGHEE_NATIVE_BINDING native addr is unknown while valid");
      if (native_valid && !$isunknown(native_addr) &&
          !bench.source_valid[native_addr])
        $error("GANGHEE_NATIVE_BINDING duplicate/phantom native result addr=%0d",
               native_addr);
      if (!$onehot0(bench.source_ready))
        $error("GANGHEE_NATIVE_BINDING acknowledged more than one source");
      if ((bench.retire_valid[0] !== native_ack) ||
          (|bench.source_ready) !== native_ack)
        $error("GANGHEE_NATIVE_BINDING acknowledge mapping is inconsistent");
      if (native_ack && native_req[native_addr])
        $error("GANGHEE_NATIVE_BINDING acknowledged req was not masked");
    end
  end
endmodule

`undef AER_GANGHEE_NATIVE_MODULE
