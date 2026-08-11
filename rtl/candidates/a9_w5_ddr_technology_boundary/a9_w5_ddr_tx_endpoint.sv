`timescale 1ns/1ps

module a9_w5_ddr_tx_endpoint #(
  parameter int ADDR_WIDTH = 4,
  parameter int DATA_WIDTH = 2
) (
  input  logic                  ref_clk_i,
  input  logic                  sample_clk_i,
  input  logic                  rst_n,
  input  logic                  launch_fire_i,
  input  logic [ADDR_WIDTH-1:0] event_addr_i,
  output logic                  frame_active_o,
  output logic                  burst_clk_o,
  output logic [DATA_WIDTH-1:0] burst_data_o
);
  logic [ADDR_WIDTH-1:0] event_addr_q;
  always_ff @(posedge ref_clk_i or negedge rst_n) begin
    if (!rst_n) begin
      event_addr_q <= '0;
      frame_active_o <= 1'b0;
    end else begin
      frame_active_o <= launch_fire_i;
      if (launch_fire_i)
        event_addr_q <= event_addr_i;
    end
  end

  a9_w5_tx_launch #(.DATA_WIDTH(DATA_WIDTH)) tx_launch (
    .ref_clk_i(ref_clk_i),
    .rst_n(rst_n),
    .launch_fire_i(launch_fire_i),
    .admitted_low_i(event_addr_i[DATA_WIDTH-1:0]),
    .held_low_i(event_addr_q[DATA_WIDTH-1:0]),
    .held_high_i(event_addr_q[ADDR_WIDTH-1 -: DATA_WIDTH]),
    .data_o(burst_data_o)
  );

  a9_w5_clock_gate clock_gate (
    .clock_i(sample_clk_i),
    .enable_i(frame_active_o),
    .rst_n(rst_n),
    .clock_o(burst_clk_o)
  );

  initial begin
    if (ADDR_WIDTH != 4)
      $fatal(1, "A9 W5 freezes N16 address width at four bits");
    if (DATA_WIDTH != 2)
      $fatal(1, "A9 W5 requires exactly two data wires");
  end
endmodule
