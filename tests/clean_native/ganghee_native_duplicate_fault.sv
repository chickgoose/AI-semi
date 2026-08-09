// Negative protocol fixture: repeat every registered native result for one
// extra cycle after the request has been acknowledged.  This models a raw
// duplicate completion and must be rejected at the unmasked native boundary.
// This fixture is test-only and is not Ganghee RTL.
`timescale 1ns/1ps

module ganghee_native_duplicate_fault (
  input  logic        clk,
  input  logic        rst,
  input  logic [15:0] req,
  output logic        valid,
  output logic [3:0]  addr
);
  integer source;
  logic sampled_valid;
  logic [3:0] sampled_addr;
  logic found;
  logic repeat_pending;

  always_comb begin
    sampled_valid = 1'b0;
    sampled_addr = '0;
    found = 1'b0;
    for (source = 0; source < 16; source = source + 1) begin
      if (!found && req[source]) begin
        sampled_valid = 1'b1;
        sampled_addr = 4'(source);
        found = 1'b1;
      end
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      valid <= 1'b0;
      addr <= '0;
      repeat_pending <= 1'b0;
    end else if (repeat_pending) begin
      // addr deliberately retains the previously completed source.
      valid <= 1'b1;
      repeat_pending <= 1'b0;
    end else begin
      valid <= sampled_valid;
      addr <= sampled_addr;
      repeat_pending <= sampled_valid;
    end
  end
endmodule
