`timescale 1ns/1ps

module w2_r1_ddr_tx_tech (
  input  logic       ref_clk_i,
  input  logic       sample_clk_i,
  input  logic       rst_n,
  input  logic       launch_fire_i,
  input  logic [3:0] event_addr_i,
  output logic       frame_active_o,
  output logic       burst_clk_o,
  output logic [1:0] burst_data_o
);
  logic [3:0] event_addr_q;

  always_ff @(posedge ref_clk_i or negedge rst_n) begin
    if (!rst_n) begin
      event_addr_q <= '0;
      frame_active_o <= 1'b0;
    end else begin
      frame_active_o <= launch_fire_i;
      if (launch_fire_i) event_addr_q <= event_addr_i;
    end
  end

  for (genvar bit_index = 0; bit_index < 2; bit_index++) begin : gen_symbol_mux
    w2_r1_mux2 symbol_mux (
      .data0_i(event_addr_q[bit_index + 2]),
      .data1_i(event_addr_q[bit_index]),
      .select_i(ref_clk_i),
      .data_o(burst_data_o[bit_index])
    );
  end

  w2_r1_clock_boundary clock_boundary (
    .clock_i(sample_clk_i), .enable_i(frame_active_o), .rst_n, .clock_o(burst_clk_o)
  );
endmodule
