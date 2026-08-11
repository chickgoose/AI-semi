`timescale 1ns/1ps

// Generic synthesizable model of the mandatory characterized ICG boundary.
module a7_r1_icg_boundary (
  input  logic clock_i,
  input  logic enable_i,
  input  logic rst_n,
  output logic clock_o
);
  logic enable_latched_q;

  always_latch begin
    if (!rst_n)
      enable_latched_q = 1'b0;
    else if (!clock_i)
      enable_latched_q = enable_i;
  end

  // Mid-high reset can truncate this modeled clock and is contract-invalid.
  assign clock_o = clock_i & enable_latched_q & rst_n;
endmodule
