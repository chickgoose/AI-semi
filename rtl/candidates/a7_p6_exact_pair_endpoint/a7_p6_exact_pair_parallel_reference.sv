`timescale 1ns/1ps

// Equal-handshake, equal-observer-latency parallel reference.  The physical
// link exposes one strobe, one pair flag, and two four-bit addresses: ten
// functional signals versus P6's forwarded clock plus five DDR data signals.
module a7_p6_exact_pair_parallel_reference (
  input  logic       ref_clk_i,
  input  logic       sample_clk_i,
  input  logic       rst_n,
  input  logic       input_valid_i,
  input  logic [1:0] input_count_i,
  input  logic [3:0] input_addr0_i,
  input  logic [3:0] input_addr1_i,
  output logic       input_ready_o,
  output logic       input_protocol_error_o,
  output logic       parallel_strobe_o,
  output logic       parallel_pair_o,
  output logic [3:0] parallel_addr0_o,
  output logic [3:0] parallel_addr1_o,
  output logic [1:0] retire_valid_o,
  output logic [3:0] retire_addr0_o,
  output logic [3:0] retire_addr1_o,
  output logic       retire_protocol_error_o,
  output logic       drain_idle_o
);
  logic launch_fire;
  logic frame_active_q;
  logic [1:0] raw_count;
  logic [3:0] raw_addr0;
  logic [3:0] raw_addr1;
  logic raw_toggle;
  logic seen_toggle;

  a7_p6_pair_launch launch (
    .ref_clk_i, .rst_n, .input_valid_i, .input_count_i,
    .input_ready_o, .launch_fire_o(launch_fire),
    .input_protocol_error_o
  );

  always_ff @(posedge ref_clk_i or negedge rst_n) begin
    if (!rst_n) begin
      frame_active_q <= 1'b0;
      parallel_pair_o <= 1'b0;
      parallel_addr0_o <= '0;
      parallel_addr1_o <= '0;
    end else begin
      frame_active_q <= launch_fire;
      if (launch_fire) begin
        parallel_pair_o <= (input_count_i == 2'd2);
        parallel_addr0_o <= input_addr0_i;
        parallel_addr1_o <= (input_count_i == 2'd2) ?
                            input_addr1_i : 4'd0;
      end
    end
  end

  assign parallel_strobe_o = sample_clk_i & frame_active_q & rst_n;

  // Commit at strobe fall to match the P6 closing edge and observer latency.
  always_ff @(negedge parallel_strobe_o or negedge rst_n) begin
    if (!rst_n) begin
      raw_count <= '0;
      raw_addr0 <= '0;
      raw_addr1 <= '0;
      raw_toggle <= 1'b0;
    end else begin
      raw_count <= parallel_pair_o ? 2'd2 : 2'd1;
      raw_addr0 <= parallel_addr0_o;
      raw_addr1 <= parallel_pair_o ? parallel_addr1_o : 4'd0;
      raw_toggle <= ~raw_toggle;
    end
  end

  a7_p6_pair_observer observer (
    .ref_clk_i, .rst_n, .raw_count_i(raw_count),
    .raw_addr0_i(raw_addr0), .raw_addr1_i(raw_addr1),
    .raw_toggle_i(raw_toggle), .raw_protocol_error_i(1'b0),
    .retire_valid_o, .retire_addr0_o, .retire_addr1_o,
    .retire_protocol_error_o, .seen_toggle_o(seen_toggle)
  );

  assign drain_idle_o = !launch_fire && !frame_active_q &&
                        !parallel_strobe_o &&
                        !(raw_toggle ^ seen_toggle) &&
                        (retire_valid_o == 2'b00);
endmodule
