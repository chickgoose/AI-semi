`timescale 1ns/1ps

// NEGATIVE_TEST_ONLY: emits one registered result without any request.  This
// deliberately violates the external fovea contract so the composition's raw
// fault visibility and unmasked phantom retirement can be tested fail-closed.
module a7_weighted_fovea_stale_no_live_fixture (
  input  logic        clk,
  input  logic        rst,
  input  logic [15:0] req,
  output logic        valid,
  output logic [3:0]  addr
);
  logic [2:0] cycle_q;

  always_ff @(posedge clk) begin
    if (rst) begin
      cycle_q <= '0;
      valid <= 1'b0;
      addr <= 4'ha;
    end else begin
      cycle_q <= cycle_q + 1'b1;
      valid <= (cycle_q == 3'd2);
      addr <= 4'ha;
    end
  end

  // Consume the native input only to keep lint focused on the deliberate
  // protocol violation; it must never affect the injected result.
  logic unused_req;
  assign unused_req = ^req;
endmodule
