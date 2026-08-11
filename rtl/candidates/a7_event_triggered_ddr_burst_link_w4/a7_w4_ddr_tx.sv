`timescale 1ns/1ps

module a7_w4_ddr_tx #(
  parameter int ADDR_WIDTH = 4,
  parameter int DATA_WIDTH = 2
) (
  input  logic                  ref_clk_i,
  input  logic                  sample_clk_i,
  input  logic                  rst_n,
  input  logic                  event_valid_i,
  input  logic [ADDR_WIDTH-1:0] event_addr_i,
  output logic                  event_ready_o,
  output logic                  burst_clk_o,
  output logic [DATA_WIDTH-1:0] burst_data_o
);
  logic [ADDR_WIDTH-1:0] event_addr_q;
  logic frame_enable_q;

  assign event_ready_o = rst_n;

  // ref_clk_i rises while sample_clk_i is low under the frozen 90-degree
  // phase contract. Address and enable therefore settle before the ICG latch
  // closes and before the forwarded sampling edge.
  always_ff @(posedge ref_clk_i or negedge rst_n) begin
    if (!rst_n) begin
      event_addr_q <= '0;
      frame_enable_q <= 1'b0;
    end else begin
      frame_enable_q <= event_valid_i;
      if (event_valid_i)
        event_addr_q <= event_addr_i;
    end
  end

  // This is a data mux, not a generated clock. Physical integration may map
  // it and the output registers into a dual-data-rate source-synchronous
  // launch macro while preserving the same quarter-cycle setup relationship.
  always_comb begin
    if (ref_clk_i)
      burst_data_o = event_addr_q[DATA_WIDTH-1:0];
    else
      burst_data_o = event_addr_q[ADDR_WIDTH-1 -: DATA_WIDTH];
  end

  a7_w4_icg_boundary clock_boundary (
    .clock_i(sample_clk_i),
    .enable_i(frame_enable_q),
    .rst_n(rst_n),
    .clock_o(burst_clk_o)
  );

  initial begin
    if (ADDR_WIDTH != 4)
      $fatal(1, "A7 W4 link freezes N16 address width at four bits");
    if (DATA_WIDTH != 2)
      $fatal(1, "A7 W4 link requires exactly two data wires");
  end
endmodule
