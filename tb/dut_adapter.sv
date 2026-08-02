// Adapter for the fixed-priority baseline, controlled experiments, and mock.
module dut_adapter #(
  parameter int NUM_SOURCES = 4,
  parameter int ADDR_WIDTH  = 16,
  parameter int FIFO_DEPTH  = 4
) (aer_if.dut bus);
`ifdef AER_DUT_A23_EE430
  a23_ee430_dut #(
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
`elsif AER_DUT_BASELINE
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
