// This is the only place that knows the baseline/improved RTL port shapes.
module dut_adapter #(
  parameter int NUM_SOURCES = 4,
  parameter int ADDR_WIDTH  = 16,
  parameter int FIFO_DEPTH  = 4,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_if.dut bus);
`ifdef AER_DUT_BASELINE
  logic [ADDR_WIDTH-1:0] baseline_addr;

  aer_baseline_top #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH)
  ) u_baseline (
    .clk_i(bus.clk),
    .rst_ni(bus.rst_n),
    .source_req_i(bus.in_valid),
    .source_ack_o(bus.in_ready),
    .event_valid_o(bus.out_valid),
    .event_ready_i(bus.out_ready),
    .event_addr_o(baseline_addr)
  );
  assign bus.out_addr = baseline_addr;
  assign bus.out_src = baseline_addr[SOURCE_WIDTH-1:0];
`elsif AER_DUT_IMPROVED
  logic [NUM_SOURCES-1:0][ADDR_WIDTH-1:0] improved_addr;
  logic [NUM_SOURCES-1:0][$clog2(FIFO_DEPTH+1)-1:0] unused_occupancy;

  genvar source;
  generate
    for (source = 0; source < NUM_SOURCES; source = source + 1) begin : pack_addr
      assign improved_addr[source] = bus.in_addr[source];
    end
  endgenerate

  aer_event_buffer #(
    .NUM_SOURCES(NUM_SOURCES),
    .ADDR_WIDTH(ADDR_WIDTH),
    .FIFO_DEPTH(FIFO_DEPTH)
  ) u_improved (
    .clk_i(bus.clk),
    .rst_ni(bus.rst_n),
    .src_valid_i(bus.in_valid),
    .src_ready_o(bus.in_ready),
    .src_addr_i(improved_addr),
    .event_valid_o(bus.out_valid),
    .event_ready_i(bus.out_ready),
    .event_addr_o(bus.out_addr),
    .event_source_o(bus.out_src),
    .occupancy_o(unused_occupancy)
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
