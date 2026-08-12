`timescale 1ns/1ps

// Optional post-scheduler contract.  It accepts each scheduler offer as one
// atomic transaction, then permits only ordered downstream retirement.  A
// partial head retirement compacts the younger item to lane 0; that movement
// never feeds scheduler policy.
module k2_ordered_link (
  input  logic       clk,
  input  logic       rst,
  input  logic [1:0] offer_count,
  input  logic [3:0] offer_addr0,
  input  logic [3:0] offer_addr1,
  output logic       offer_ready,
  output logic [1:0] retire_valid,
  output logic [3:0] retire_addr0,
  output logic [3:0] retire_addr1,
  input  logic [1:0] retire_ready,
  output logic       link_empty
);
  logic [1:0] count_q, count_n;
  logic [3:0] addr0_q, addr0_n;
  logic [3:0] addr1_q, addr1_n;
  logic [1:0] retire_count;
  logic [1:0] remaining_count;
  logic offer_fire;

  always @* begin
    retire_valid[0] = count_q != 0;
    retire_valid[1] = (count_q == 2) && retire_ready[0] && retire_ready[1];
    retire_addr0 = addr0_q;
    retire_addr1 = addr1_q;

`ifdef K2_LINK_MUT_YOUNGER_BYPASS
    retire_valid[1] = (count_q == 2) && retire_ready[1];
`endif
`ifdef K2_LINK_MUT_REORDER
    if (count_q == 2) begin
      retire_addr0 = addr1_q;
      retire_addr1 = addr0_q;
    end
`endif

    retire_count = 0;
    if ((count_q != 0) && retire_ready[0]) begin
      retire_count = 1;
      if ((count_q == 2) && retire_ready[1])
        retire_count = 2;
    end
    remaining_count = count_q - retire_count;
    offer_ready = offer_count <= (2'd2 - remaining_count);
`ifdef K2_LINK_MUT_REFILL_BUBBLE
    offer_ready = offer_count <= (2'd2 - count_q);
`endif
    offer_fire = offer_ready && (offer_count != 0);

    count_n = remaining_count;
    addr0_n = 0;
    addr1_n = 0;
    case (retire_count)
      0: begin
        addr0_n = addr0_q;
        addr1_n = addr1_q;
      end
      1: addr0_n = addr1_q;
      default: begin end
    endcase
`ifdef K2_LINK_MUT_NO_COMPACT
    if (retire_count == 1)
      addr0_n = addr0_q;
`endif

    if (offer_fire) begin
      if (remaining_count == 0) begin
        addr0_n = offer_addr0;
        if (offer_count == 2)
          addr1_n = offer_addr1;
      end else begin
        addr1_n = offer_addr0;
      end
      count_n = remaining_count + offer_count;
    end
  end

  always @(posedge clk) begin
    if (rst) begin
`ifndef K2_LINK_MUT_RESET_STALE
      count_q <= 0;
      addr0_q <= 0;
      addr1_q <= 0;
`endif
    end else begin
      count_q <= count_n;
      addr0_q <= addr0_n;
      addr1_q <= addr1_n;
    end
  end

  assign link_empty = count_q == 0;
endmodule
