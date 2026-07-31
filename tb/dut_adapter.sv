// This is the only file that should need signal-name/shape changes for a real DUT.
// Compile with +define+AER_EXTERNAL_DUT and provide a module named aer_dut whose
// ports obey docs/tasks/a3.md. Without that define, a smoke-test model is used.
module dut_adapter #(
  parameter int NUM_SOURCES = 4,
  parameter int ADDR_WIDTH  = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_if.dut bus);
`ifdef AER_EXTERNAL_DUT
  aer_dut #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH)
  ) u_dut (
    .clk(bus.clk),
    .rst_n(bus.rst_n),
    .in_valid(bus.in_valid),
    .in_ready(bus.in_ready),
    .in_addr(bus.in_addr),
    .out_valid(bus.out_valid),
    .out_ready(bus.out_ready),
    .out_addr(bus.out_addr),
    .out_src(bus.out_src)
  );
`else
  aer_mock_dut #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH)
  ) u_smoke_dut (
    .clk(bus.clk),
    .rst_n(bus.rst_n),
    .in_valid(bus.in_valid),
    .in_ready(bus.in_ready),
    .in_addr(bus.in_addr),
    .out_valid(bus.out_valid),
    .out_ready(bus.out_ready),
    .out_addr(bus.out_addr),
    .out_src(bus.out_src)
  );
`endif
endmodule
