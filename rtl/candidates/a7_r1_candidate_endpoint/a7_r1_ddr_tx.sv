`timescale 1ns/1ps

module a7_r1_ddr_tx (
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
      if (launch_fire_i)
        event_addr_q <= event_addr_i;
    end
  end

  assign burst_data_o = ref_clk_i ? event_addr_q[1:0] : event_addr_q[3:2];

  a7_r1_icg_boundary clock_boundary (
    .clock_i(sample_clk_i),
    .enable_i(frame_active_o),
    .rst_n(rst_n),
    .clock_o(burst_clk_o)
  );
endmodule
