`timescale 1ns/1ps

// Charged post-scheduler transport for A5's independently controlled retire
// observations.  The owner scheduler still commits one complete registered
// offer through offer_ready; retire-side movement never feeds policy state.
//
// The two entries are an ordered FIFO.  Lane 1 is exposed only on an edge on
// which lane 0 and lane 1 can both retire.  This makes the A5 observation rule
// (valid[lane] && ready[lane]) incapable of retiring the younger entry around
// a blocked head.  A hidden younger entry compacts to lane 0 after the head
// retires.  This ready-qualified presentation is an A5 link convention, not a
// scheduler-boundary semantic.
module a3_k2_ordered_link_adapter (
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
  logic       offer_fire;

  always @* begin
    retire_valid[0] = (count_q != 0);
`ifdef A3_K2_LINK_MUT_BYPASS
    // Mutation: expose the younger lane whenever only its sink is ready.
    retire_valid[1] = (count_q == 2) && retire_ready[1];
`else
    retire_valid[1] = (count_q == 2) && retire_ready[0] && retire_ready[1];
`endif
    retire_addr0 = addr0_q;
    retire_addr1 = addr1_q;

    retire_count = 0;
    if ((count_q != 0) && retire_ready[0]) begin
      retire_count = 1;
      if ((count_q == 2) && retire_ready[1])
        retire_count = 2;
    end
    remaining_count = count_q - retire_count;
`ifdef A3_K2_LINK_MUT_OVERFLOW
    // Mutation: advertise acceptance while a full, stalled queue has no room.
    offer_ready = (offer_count <= (2'd2 - remaining_count)) ||
                  ((count_q == 2) && (retire_count == 0) && (offer_count != 0));
`else
    offer_ready = (offer_count <= (2'd2 - remaining_count));
`endif
`ifdef A3_K2_LINK_MUT_REFILL
    // Mutation: drop an otherwise legal offer on a simultaneous retirement.
    offer_fire = offer_ready && (offer_count != 0) && (retire_count == 0);
`else
    offer_fire = offer_ready && (offer_count != 0);
`endif

    count_n = remaining_count;
    addr0_n = 0;
    addr1_n = 0;
    case (retire_count)
      0: begin
        addr0_n = addr0_q;
        addr1_n = addr1_q;
      end
      1: begin
`ifdef A3_K2_LINK_MUT_ORDER
        // Mutation: retain the retired head instead of compacting the tail.
        addr0_n = addr0_q;
`else
        addr0_n = addr1_q;
`endif
      end
      default: begin end
    endcase

    if (offer_fire) begin
      if (remaining_count == 0) begin
        addr0_n = offer_addr0;
        if (offer_count == 2)
          addr1_n = offer_addr1;
      end else begin
        // Capacity arithmetic guarantees a one-entry offer here.
        addr1_n = offer_addr0;
      end
      count_n = remaining_count + offer_count;
    end
  end

  always @(posedge clk) begin
    if (rst) begin
`ifdef A3_K2_LINK_MUT_RESET
      // Mutation: reset leaves a fabricated occupied entry.
      count_q <= 1;
`else
      count_q <= 0;
`endif
      addr0_q <= 0;
      addr1_q <= 0;
    end else begin
      count_q <= count_n;
      addr0_q <= addr0_n;
      addr1_q <= addr1_n;
    end
  end

  assign link_empty = (count_q == 0);

`ifndef SYNTHESIS
  always @(posedge clk) begin
    if (!rst) begin
      if (count_q > 2)
        $fatal(1, "A3_K2_LINK illegal count=%0d", count_q);
      if (retire_valid[1] && !retire_valid[0])
        $fatal(1, "A3_K2_LINK lane hole");
      if ((count_q == 2) && !retire_ready[0] && retire_valid[1])
        $fatal(1, "A3_K2_LINK younger bypass exposure");
      if ((remaining_count == 1) && offer_fire && (offer_count != 1))
        $fatal(1, "A3_K2_LINK non-fitting offer accepted");
    end
  end
`endif
endmodule
