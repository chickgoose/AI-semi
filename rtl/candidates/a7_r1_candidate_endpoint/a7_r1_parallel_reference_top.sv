`timescale 1ns/1ps

module a7_r1_parallel_reference_top (
  input  logic       ref_clk_i,
  input  logic       sample_clk_i,
  input  logic       rst_n,
  input  logic       event_valid_i,
  input  logic [3:0] event_addr_i,
  output logic       event_ready_o,
  output logic       link_strobe_o,
  output logic [3:0] link_data_o,
  output logic [3:0] retire_addr_o,
  output logic       retire_valid_o,
  output logic       drain_idle_o
);
  logic launch_fire;
  logic frame_active_q;
  logic [3:0] raw_retire_addr;
  logic raw_retire_toggle;
  logic seen_retire_toggle;

  a7_r1_launch_qualifier launch_qualifier (
    .ref_clk_i(ref_clk_i), .rst_n(rst_n), .event_valid_i(event_valid_i),
    .event_ready_o(event_ready_o), .launch_fire_o(launch_fire));

  always_ff @(posedge ref_clk_i or negedge rst_n) begin
    if (!rst_n) begin
      frame_active_q <= 1'b0;
      link_data_o <= '0;
    end else begin
      frame_active_q <= launch_fire;
      if (launch_fire)
        link_data_o <= event_addr_i;
    end
  end

  a7_r1_icg_boundary clock_boundary (
    .clock_i(sample_clk_i), .enable_i(frame_active_q), .rst_n(rst_n),
    .clock_o(link_strobe_o));

  always_ff @(posedge link_strobe_o or negedge rst_n) begin
    if (!rst_n) begin
      raw_retire_addr <= '0;
      raw_retire_toggle <= 1'b0;
    end else begin
      raw_retire_addr <= link_data_o;
      raw_retire_toggle <= ~raw_retire_toggle;
    end
  end

  a7_r1_retire_observer retire_observer (
    .ref_clk_i(ref_clk_i), .rst_n(rst_n), .raw_addr_i(raw_retire_addr),
    .raw_toggle_i(raw_retire_toggle), .retire_addr_o(retire_addr_o),
    .retire_valid_o(retire_valid_o), .seen_toggle_o(seen_retire_toggle));

  assign drain_idle_o = ~frame_active_q & ~link_strobe_o &
                        ~(raw_retire_toggle ^ seen_retire_toggle);
endmodule
