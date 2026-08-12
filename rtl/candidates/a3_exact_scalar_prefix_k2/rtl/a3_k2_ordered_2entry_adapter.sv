`timescale 1ns/1ps

// Charged ordered transport between the atomic K2 scheduler offer and the
// common two-lane retire boundary.  Both entries, including the common event
// identity, are candidate state and therefore remain inside the PPA boundary.
//
// The head may retire by itself.  The younger entry is exposed on lane 1 only
// when both entries retire on the same edge; it can never bypass a blocked
// head.  Capacity is computed after that edge's retire handshakes, allowing a
// fitting atomic owner offer to refill without a bubble.
module a3_k2_ordered_2entry_adapter #(
  parameter int ADDR_WIDTH   = 16,
  parameter int SOURCE_WIDTH = 4
) (
  input  logic                    clk,
  input  logic                    rst,

  input  logic [1:0]              offer_count,
  input  logic [SOURCE_WIDTH-1:0] offer_source0,
  input  logic [SOURCE_WIDTH-1:0] offer_source1,
  input  logic [ADDR_WIDTH-1:0]   offer_event0,
  input  logic [ADDR_WIDTH-1:0]   offer_event1,
  output logic                    offer_ready,

  output logic [1:0]              retire_valid,
  output logic [SOURCE_WIDTH-1:0] retire_source0,
  output logic [SOURCE_WIDTH-1:0] retire_source1,
  output logic [ADDR_WIDTH-1:0]   retire_event0,
  output logic [ADDR_WIDTH-1:0]   retire_event1,
  input  logic [1:0]              retire_ready,
  output logic                    empty
);
  logic [1:0] count_q, count_n;
  logic [SOURCE_WIDTH-1:0] source0_q, source0_n;
  logic [SOURCE_WIDTH-1:0] source1_q, source1_n;
  logic [ADDR_WIDTH-1:0] event0_q, event0_n;
  logic [ADDR_WIDTH-1:0] event1_q, event1_n;
  logic [1:0] retire_count;
  logic [1:0] remaining_count;
  logic       offer_fire;

  always @* begin
    // Lane 0 is always the oldest buffered event.  A valid lane 1 implies
    // that both lanes handshake together on this edge.
    retire_valid[0] = (count_q != 2'd0);
    retire_valid[1] = (count_q == 2'd2) &&
                      retire_ready[0] && retire_ready[1];
    retire_source0 = source0_q;
    retire_source1 = source1_q;
    retire_event0 = event0_q;
    retire_event1 = event1_q;

    retire_count = 2'd0;
    if ((count_q != 2'd0) && retire_ready[0]) begin
      retire_count = 2'd1;
      if ((count_q == 2'd2) && retire_ready[1])
        retire_count = 2'd2;
    end

    remaining_count = count_q - retire_count;
    offer_ready = (offer_count <= (2'd2 - remaining_count));
    offer_fire = offer_ready && (offer_count != 2'd0);

    count_n = remaining_count;
    source0_n = '0;
    source1_n = '0;
    event0_n = '0;
    event1_n = '0;

    case (retire_count)
      2'd0: begin
        source0_n = source0_q;
        source1_n = source1_q;
        event0_n = event0_q;
        event1_n = event1_q;
      end
      2'd1: begin
        source0_n = source1_q;
        event0_n = event1_q;
      end
      default: begin end
    endcase

    if (offer_fire) begin
      if (remaining_count == 2'd0) begin
        source0_n = offer_source0;
        event0_n = offer_event0;
        if (offer_count == 2'd2) begin
          source1_n = offer_source1;
          event1_n = offer_event1;
        end
      end else begin
        // Capacity arithmetic permits only a one-entry offer in this case.
        source1_n = offer_source0;
        event1_n = offer_event0;
      end
      count_n = remaining_count + offer_count;
    end
  end

  always @(posedge clk) begin
    if (rst) begin
      count_q <= 2'd0;
      source0_q <= '0;
      source1_q <= '0;
      event0_q <= '0;
      event1_q <= '0;
    end else begin
      count_q <= count_n;
      source0_q <= source0_n;
      source1_q <= source1_n;
      event0_q <= event0_n;
      event1_q <= event1_n;
    end
  end

  assign empty = (count_q == 2'd0);

`ifndef SYNTHESIS
  always @(posedge clk) begin
    if (!rst) begin
      if (offer_count > 2'd2)
        $fatal(1, "A3_K2_ADAPTER illegal offer_count=%0d", offer_count);
      if (count_q > 2'd2)
        $fatal(1, "A3_K2_ADAPTER illegal buffered count=%0d", count_q);
      if (retire_valid[1] && !retire_valid[0])
        $fatal(1, "A3_K2_ADAPTER retire lane hole");
      if ((count_q == 2'd2) && !retire_ready[0] && retire_valid[1])
        $fatal(1, "A3_K2_ADAPTER younger event bypassed head");
      if ((remaining_count == 2'd1) && offer_fire &&
          (offer_count != 2'd1))
        $fatal(1, "A3_K2_ADAPTER accepted a non-fitting owner offer");
    end
  end
`endif
endmodule
