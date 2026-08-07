`timescale 1ns/1ps

module a8_unsafe_stall_param_tb;
  logic clk = 1'b0;
  logic rst_n = 1'b0;
  logic [3:0] request = '0;
  logic [3:0] grant;

  always #5 clk = ~clk;

  // Horizon 8 is not strictly greater than (N-1)+stall_bound = 8.
  a8_age_calendar_wheel_arbiter #(
    .NUM_SOURCES(4), .BUCKET_CYCLES(2), .EPOCH_COUNT(4),
    .MAX_STALL_CYCLES(5)
  ) unsafe (
    .clk(clk), .rst_n(rst_n), .request(request), .advance(1'b0),
    .grant(grant), .tracked_debug(), .epoch_debug()
  );
endmodule
