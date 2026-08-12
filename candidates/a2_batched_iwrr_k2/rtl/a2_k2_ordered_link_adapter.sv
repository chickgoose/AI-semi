`timescale 1ns/1ps

// Charged post-scheduler storage for the normalized two-lane retire seam.
//
// An owner offer is accepted only when the complete 0/1/2-entry bundle fits.
// The two entries form an ordered FIFO.  Lane 1 is exposed only when both
// entries retire on the same edge; otherwise the head retires alone and the
// younger entry compacts to lane 0.  Retire progress cannot feed policy state.
module a2_k2_ordered_link_adapter #(
  parameter int EVENT_WIDTH = 16,
  parameter int SOURCE_WIDTH = 4
) (
  input  logic                    clk,
  input  logic                    rst,

  input  logic [1:0]              offer_count,
  input  logic [EVENT_WIDTH-1:0]  offer_event0,
  input  logic [EVENT_WIDTH-1:0]  offer_event1,
  input  logic [SOURCE_WIDTH-1:0] offer_source0,
  input  logic [SOURCE_WIDTH-1:0] offer_source1,
  output logic                    offer_ready,

  output logic [1:0]              retire_valid,
  output logic [EVENT_WIDTH-1:0]  retire_event0,
  output logic [EVENT_WIDTH-1:0]  retire_event1,
  output logic [SOURCE_WIDTH-1:0] retire_source0,
  output logic [SOURCE_WIDTH-1:0] retire_source1,
  input  logic [1:0]              retire_ready,
  output logic                    link_empty
);
  logic [1:0] count_q, count_n;
  logic [EVENT_WIDTH-1:0] event0_q, event0_n;
  logic [EVENT_WIDTH-1:0] event1_q, event1_n;
  logic [SOURCE_WIDTH-1:0] source0_q, source0_n;
  logic [SOURCE_WIDTH-1:0] source1_q, source1_n;
  logic [1:0] retire_count;
  logic [1:0] remaining_count;
  logic       offer_fire;

  always_comb begin
    // Prevent a ready younger lane from observing an out-of-order transfer.
    retire_valid[0] = (count_q != 0);
    retire_valid[1] = (count_q == 2) && retire_ready[0] && retire_ready[1];
    retire_event0 = event0_q;
    retire_event1 = event1_q;
    retire_source0 = source0_q;
    retire_source1 = source1_q;

    retire_count = 0;
    if ((count_q != 0) && retire_ready[0]) begin
      retire_count = 1;
      if ((count_q == 2) && retire_ready[1])
        retire_count = 2;
    end
    remaining_count = count_q - retire_count;

    // This is the sole owner acceptance condition.  In particular, a K2
    // offer cannot commit into one free entry.
    offer_ready = (offer_count <= (2'd2 - remaining_count));
    offer_fire = offer_ready && (offer_count != 0);

    count_n = remaining_count;
    event0_n = '0;
    event1_n = '0;
    source0_n = '0;
    source1_n = '0;
    case (retire_count)
      0: begin
        event0_n = event0_q;
        event1_n = event1_q;
        source0_n = source0_q;
        source1_n = source1_q;
      end
      1: begin
        event0_n = event1_q;
        source0_n = source1_q;
      end
      default: begin end
    endcase

    if (offer_fire) begin
      if (remaining_count == 0) begin
        event0_n = offer_event0;
        source0_n = offer_source0;
        if (offer_count == 2) begin
          event1_n = offer_event1;
          source1_n = offer_source1;
        end
      end else begin
        // Capacity arithmetic permits only a one-entry offer in this case.
        event1_n = offer_event0;
        source1_n = offer_source0;
      end
      count_n = remaining_count + offer_count;
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      count_q <= 0;
      event0_q <= '0;
      event1_q <= '0;
      source0_q <= '0;
      source1_q <= '0;
    end else begin
      count_q <= count_n;
      event0_q <= event0_n;
      event1_q <= event1_n;
      source0_q <= source0_n;
      source1_q <= source1_n;
    end
  end

  assign link_empty = (count_q == 0);

`ifndef SYNTHESIS
  always_ff @(posedge clk) begin
    if (!rst) begin
      if (count_q > 2)
        $fatal(1, "A2_K2_LINK illegal count=%0d", count_q);
      if (offer_count > 2)
        $fatal(1, "A2_K2_LINK illegal offer_count=%0d", offer_count);
      if (retire_valid[1] && !retire_valid[0])
        $fatal(1, "A2_K2_LINK lane hole");
      if ((count_q == 2) && !retire_ready[0] && retire_valid[1])
        $fatal(1, "A2_K2_LINK younger bypass exposure");
      if ((remaining_count == 1) && offer_fire && (offer_count != 1))
        $fatal(1, "A2_K2_LINK non-fitting offer accepted");
    end
  end
`endif
endmodule
