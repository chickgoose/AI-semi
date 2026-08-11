`timescale 1ns/1ps

module a7_ddr_burst_tx #(
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
  localparam int SYMBOLS_PER_EVENT = ADDR_WIDTH / DATA_WIDTH;

  logic [ADDR_WIDTH-1:0] event_addr_q;
  logic frame_enable_q;

  // The candidate has no queue. One address is admitted on each ref-clock
  // rising edge and occupies exactly that reference period.
  assign event_ready_o = rst_n;

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

  // ref_clk_i and sample_clk_i have the same frequency. The physical
  // contract requires sample_clk_i to be shifted by one quarter period, so
  // these mux transitions are separated from both sampling edges.
  always_comb begin
    if (ref_clk_i)
      burst_data_o = event_addr_q[DATA_WIDTH-1:0];
    else
      burst_data_o = event_addr_q[ADDR_WIDTH-1 -: DATA_WIDTH];
  end

  // frame_enable_q changes only while sample_clk_i is low under the declared
  // phase contract. A real implementation must use a characterized ICG or
  // source-synchronous output cell; this RTL is the digital functional model.
  assign burst_clk_o = sample_clk_i & frame_enable_q & rst_n;

  initial begin
    if (ADDR_WIDTH != 4)
      $fatal(1, "A7 DDR burst link freezes N16 address width at four bits");
    if (DATA_WIDTH != 2)
      $fatal(1, "A7 DDR burst link requires exactly two data wires");
    if (SYMBOLS_PER_EVENT != 2)
      $fatal(1, "A7 DDR burst link requires two symbols per event");
  end
endmodule
