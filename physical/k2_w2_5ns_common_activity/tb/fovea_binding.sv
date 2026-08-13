`timescale 1ns/1ps

module aer_legacy_candidate_adapter #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int RETIRE_LANES = 2,
  parameter int FIFO_DEPTH = 0,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if.candidate bench);
  logic sample_clk_i = 1'b0;
  logic [15:0] source_accept;
  logic link_clk;
  logic [1:0] link_data;
  logic [1:0] retire_valid;
  logic [3:0] retire_addr0, retire_addr1;
  logic drain_idle, protocol_error;

  initial begin
    #3.75 sample_clk_i = 1'b1;
    forever #2.5 sample_clk_i = ~sample_clk_i;
  end

  w2_fovea_r1_physical_staging_top dut (
    .ref_clk_i(bench.clk), .sample_clk_i, .rst_n(bench.rst_n),
    .source_pending_i(bench.source_valid), .source_accept_o(source_accept),
    .link_clk_o(link_clk), .link_data_o(link_data),
    .retire_valid_o(retire_valid), .retire_addr0_o(retire_addr0),
    .retire_addr1_o(retire_addr1), .drain_idle_o(drain_idle),
    .protocol_error_o(protocol_error)
  );

  w2_activity_probe #(.CANDIDATE_ID("fovea_a7")) probe (
    .ref_clk_i(bench.clk), .sample_clk_i, .rst_n(bench.rst_n),
    .measurement_active_i(aer_clean_tb.measurement_active),
    .protocol_error_i(protocol_error), .drain_idle_i(drain_idle),
    .source_accept_i(source_accept), .retire_valid_i(retire_valid)
  );

  always_comb begin
    bench.source_ready = '0;
    bench.retire_valid = '0;
    bench.retire_event[0] = '0;
    bench.retire_event[1] = '0;
    bench.retire_source[0] = '0;
    bench.retire_source[1] = '0;
    if (bench.rst_n === 1'b1) begin
      bench.source_ready = source_accept;
      bench.retire_valid = retire_valid;
      bench.retire_event[0] = ADDR_WIDTH'(retire_addr0);
      bench.retire_event[1] = ADDR_WIDTH'(retire_addr1);
      bench.retire_source[0] = SOURCE_WIDTH'(retire_addr0);
      bench.retire_source[1] = SOURCE_WIDTH'(retire_addr1);
    end
  end

  initial begin
    if (NUM_SOURCES != 16 || ADDR_WIDTH != 16 ||
        RETIRE_LANES != 2 || FIFO_DEPTH != 0)
      $fatal(1, "W2 activity requires N16/A16/K2/FIFO0");
  end

  always @(posedge bench.clk) if (bench.rst_n === 1'b1) begin
    for (integer source = 0; source < 16; source = source + 1)
      if (bench.source_valid[source] &&
          bench.source_event[source] !== ADDR_WIDTH'(source))
        $fatal(1, "W2_ACTIVITY_ADDRESS_ONLY_VIOLATION");
    if (protocol_error !== 1'b0)
      $fatal(1, "W2_ACTIVITY_FOVEA_PROTOCOL_ERROR");
    if ((retire_valid & ~bench.retire_ready) != 0)
      $fatal(1, "W2_ACTIVITY_REQUIRES_ALWAYS_READY");
  end
endmodule
