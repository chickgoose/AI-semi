`timescale 1ns/1ps

// Technology-neutral observable contract for the A7 42377ca R1 endpoint:
// - admission on ref_clk_i rising edges;
// - address[1:0] sampled on the forwarded-clock rising edge;
// - address[3:2] sampled and one toggle committed on its falling edge;
// - reset is supported only after drain while the burst clock is low.
module a9_w5_ddr_link #(
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
  output logic [DATA_WIDTH-1:0] burst_data_o,
  output logic [ADDR_WIDTH-1:0] retire_addr_o,
  output logic                  retire_valid_o,
  output logic                  drain_idle_o
);
  logic launch_fire;
  logic frame_active;
  logic [ADDR_WIDTH-1:0] raw_retire_addr;
  logic raw_retire_toggle;
  logic seen_retire_toggle;

  a9_w5_launch_qualifier launch_qualifier (
    .ref_clk_i(ref_clk_i),
    .rst_n(rst_n),
    .event_valid_i(event_valid_i),
    .event_ready_o(event_ready_o),
    .launch_fire_o(launch_fire)
  );

  // This self-loopback composition exists for equivalence testing.  Physical
  // endpoints instantiate the TX and RX modules separately at their pads.
  a9_w5_ddr_tx_endpoint #(
    .ADDR_WIDTH(ADDR_WIDTH),
    .DATA_WIDTH(DATA_WIDTH)
  ) tx_endpoint (
    .ref_clk_i(ref_clk_i),
    .sample_clk_i(sample_clk_i),
    .rst_n(rst_n),
    .launch_fire_i(launch_fire),
    .event_addr_i(event_addr_i),
    .frame_active_o(frame_active),
    .burst_clk_o(burst_clk_o),
    .burst_data_o(burst_data_o)
  );

  a9_w5_ddr_rx_endpoint #(
    .ADDR_WIDTH(ADDR_WIDTH),
    .DATA_WIDTH(DATA_WIDTH)
  ) rx_endpoint (
    .burst_clk_i(burst_clk_o),
    .burst_data_i(burst_data_o),
    .rst_n(rst_n),
    .raw_retire_addr_o(raw_retire_addr),
    .raw_retire_toggle_o(raw_retire_toggle)
  );

  a9_w5_retire_observer retire_observer (
    .ref_clk_i(ref_clk_i),
    .rst_n(rst_n),
    .raw_addr_i(raw_retire_addr),
    .raw_toggle_i(raw_retire_toggle),
    .retire_addr_o(retire_addr_o),
    .retire_valid_o(retire_valid_o),
    .seen_toggle_o(seen_retire_toggle)
  );

  // Fail closed for a combinational same-cycle launch and for the registered
  // valid cycle that the always-ready synchronous consumer has not sampled.
  assign drain_idle_o = ~launch_fire & ~frame_active & ~burst_clk_o &
                        ~(raw_retire_toggle ^ seen_retire_toggle) &
                        ~retire_valid_o;
endmodule
