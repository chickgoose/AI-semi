`timescale 1ns/1ps

module a7_w4_parallel4_reference (
  input logic ref_clk_i, sample_clk_i, rst_n, event_valid_i,
  input logic [3:0] event_addr_i,
  output logic event_ready_o, link_clk_o,
  output logic [3:0] link_data_o, retire_addr_o,
  output logic retire_toggle_o
);
  logic enable_q;
  always_ff @(posedge ref_clk_i or negedge rst_n) begin
    if (!rst_n) begin enable_q <= 1'b0; link_data_o <= '0; end
    else begin
      enable_q <= event_valid_i;
      if (event_valid_i) link_data_o <= event_addr_i;
    end
  end
  assign event_ready_o = rst_n;
  a7_w4_icg_boundary gate (.clock_i(sample_clk_i), .enable_i(enable_q),
                           .rst_n(rst_n), .clock_o(link_clk_o));
  always_ff @(posedge link_clk_o or negedge rst_n) begin
    if (!rst_n) begin retire_addr_o <= '0; retire_toggle_o <= 1'b0; end
    else begin retire_addr_o <= link_data_o; retire_toggle_o <= ~retire_toggle_o; end
  end
endmodule

module a7_w4_serial1_reference (
  input logic ref_clk_i, sample_clk_i, rst_n, event_valid_i,
  input logic [3:0] event_addr_i,
  output logic event_ready_o, link_clk_o, link_data_o,
  output logic [3:0] retire_addr_o,
  output logic retire_toggle_o
);
  logic [3:0] address_q;
  logic busy_q, second_pair_q;
  logic rise_bit_q;
  logic [1:0] first_pair_q;
  logic rx_second_pair_q;

  assign event_ready_o = rst_n & ~busy_q;
  always_ff @(posedge ref_clk_i or negedge rst_n) begin
    if (!rst_n) begin address_q <= '0; busy_q <= 1'b0; second_pair_q <= 1'b0; end
    else if (!busy_q) begin
      if (event_valid_i) begin address_q <= event_addr_i; busy_q <= 1'b1; end
      second_pair_q <= 1'b0;
    end else if (!second_pair_q) second_pair_q <= 1'b1;
    else begin busy_q <= 1'b0; second_pair_q <= 1'b0; end
  end
  assign link_data_o = ref_clk_i ?
    (second_pair_q ? address_q[2] : address_q[0]) :
    (second_pair_q ? address_q[3] : address_q[1]);
  a7_w4_icg_boundary gate (.clock_i(sample_clk_i), .enable_i(busy_q),
                           .rst_n(rst_n), .clock_o(link_clk_o));
  always_ff @(posedge link_clk_o or negedge rst_n) begin
    if (!rst_n) rise_bit_q <= 1'b0; else rise_bit_q <= link_data_o;
  end
  always_ff @(negedge link_clk_o or negedge rst_n) begin
    if (!rst_n) begin
      first_pair_q <= '0; rx_second_pair_q <= 1'b0;
      retire_addr_o <= '0; retire_toggle_o <= 1'b0;
    end else if (!rx_second_pair_q) begin
      first_pair_q <= {link_data_o, rise_bit_q};
      rx_second_pair_q <= 1'b1;
    end else begin
      retire_addr_o <= {link_data_o, rise_bit_q, first_pair_q};
      retire_toggle_o <= ~retire_toggle_o;
      rx_second_pair_q <= 1'b0;
    end
  end
endmodule

module a7_w4_structural_compare_top #(
  parameter int STYLE = 1
) (
  input logic ref_clk_i, sample_clk_i, rst_n, event_valid_i,
  input logic [3:0] event_addr_i,
  output logic event_ready_o, link_clk_o,
  output logic [3:0] link_data_observe_o, retire_addr_o,
  output logic retire_toggle_o
);
  generate
    if (STYLE == 0) begin : parallel4
      a7_w4_parallel4_reference impl (
        .ref_clk_i, .sample_clk_i, .rst_n, .event_valid_i, .event_addr_i,
        .event_ready_o, .link_clk_o, .link_data_o(link_data_observe_o),
        .retire_addr_o, .retire_toggle_o);
    end else if (STYLE == 1) begin : ddr2
      logic [1:0] data;
      a7_event_triggered_ddr_burst_link_w4 impl (
        .ref_clk_i, .sample_clk_i, .rst_n, .event_valid_i, .event_addr_i,
        .event_ready_o, .burst_clk_o(link_clk_o), .burst_data_o(data),
        .retire_addr_o, .retire_toggle_o);
      assign link_data_observe_o = {2'b00, data};
    end else begin : serial1
      logic data;
      a7_w4_serial1_reference impl (
        .ref_clk_i, .sample_clk_i, .rst_n, .event_valid_i, .event_addr_i,
        .event_ready_o, .link_clk_o, .link_data_o(data),
        .retire_addr_o, .retire_toggle_o);
      assign link_data_observe_o = {3'b000, data};
    end
  endgenerate
endmodule
