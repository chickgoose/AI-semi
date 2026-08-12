`timescale 1ns/1ps

// Passive checks that are valid for every N=16 atomic K2 scheduler policy.
// Exact offer latency and directed transaction sequences live in the vector
// harness because those checks need the owner's declared latency stamp.
module k2_conformance_oracle (
  input logic        clk,
  input logic        rst,
  input logic [15:0] source_pending,
  input logic [1:0]  grant_count,
  input logic [3:0]  grant_addr0,
  input logic [3:0]  grant_addr1,
  input logic        bundle_ready,
  input logic        drain_idle
);
  logic       stalled_q;
  logic [1:0] held_count_q;
  logic [3:0] held_addr0_q;
  logic [3:0] held_addr1_q;

  always @(posedge clk) begin
    if (rst) begin
      stalled_q <= 1'b0;
      held_count_q <= 0;
      held_addr0_q <= 0;
      held_addr1_q <= 0;
    end else begin
      if (^grant_count === 1'bx)
        $fatal(1, "K2_ORACLE count contains X/Z");
      if (grant_count > 2)
        $fatal(1, "K2_ORACLE illegal count=%0d", grant_count);
      if ((grant_count != 0) && (^grant_addr0 === 1'bx))
        $fatal(1, "K2_ORACLE lane0 address contains X/Z");
      if ((grant_count == 2) && (^grant_addr1 === 1'bx))
        $fatal(1, "K2_ORACLE lane1 address contains X/Z");
      if ((grant_count != 0) && !source_pending[grant_addr0])
        $fatal(1, "K2_ORACLE phantom lane0 addr=%0d pending=%h",
               grant_addr0, source_pending);
      if ((grant_count == 2) && !source_pending[grant_addr1])
        $fatal(1, "K2_ORACLE phantom lane1 addr=%0d pending=%h",
               grant_addr1, source_pending);
      if ((grant_count == 2) && (grant_addr0 == grant_addr1))
        $fatal(1, "K2_ORACLE duplicate pair addr=%0d", grant_addr0);
      if (stalled_q && ((grant_count !== held_count_q) ||
          (grant_addr0 !== held_addr0_q) ||
          ((held_count_q == 2) && (grant_addr1 !== held_addr1_q))))
        $fatal(1, "K2_ORACLE held offer changed");
      if (drain_idle !== ((source_pending == 0) && (grant_count == 0)))
        $fatal(1, "K2_ORACLE drain_idle is not truthful");

      stalled_q <= (grant_count != 0) && !bundle_ready;
      held_count_q <= grant_count;
      held_addr0_q <= grant_addr0;
      held_addr1_q <= grant_addr1;
    end
  end
endmodule
