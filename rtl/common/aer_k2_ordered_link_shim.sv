`timescale 1ns/1ps

// Candidate-neutral charged transport from an atomic K2 scheduler offer to
// the normalized common AER retire seam.  The scheduler owns winner policy;
// this module owns all post-scheduler event/source storage and must therefore
// remain inside the candidate PPA boundary.
module aer_k2_ordered_link_shim #(
  parameter int NUM_SOURCES  = 16,
  parameter int EVENT_WIDTH  = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES),
  parameter int RETIRE_LANES = aer_k2_binding_pkg::K2_RETIRE_LANES
) (
  input  logic                         clk,
  input  logic                         rst_n,

  input  logic [NUM_SOURCES-1:0]       source_valid,
  output logic [NUM_SOURCES-1:0]       source_ready,
  input  logic [EVENT_WIDTH-1:0]       source_event [NUM_SOURCES],

  input  logic [1:0]                   offer_count,
  input  logic [SOURCE_WIDTH-1:0]      offer_source0,
  input  logic [SOURCE_WIDTH-1:0]      offer_source1,
  output logic                         offer_ready,
  input  logic                         scheduler_idle,

  output logic [RETIRE_LANES-1:0]      retire_valid,
  input  logic [RETIRE_LANES-1:0]      retire_ready,
  output logic [EVENT_WIDTH-1:0]       retire_event [RETIRE_LANES],
  output logic [SOURCE_WIDTH-1:0]      retire_source [RETIRE_LANES],

  output logic                         link_empty,
  output logic                         drain_idle
);
  import aer_k2_binding_pkg::*;

  logic [1:0] count_q, count_n;
  logic [EVENT_WIDTH-1:0] event0_q, event0_n;
  logic [EVENT_WIDTH-1:0] event1_q, event1_n;
  logic [SOURCE_WIDTH-1:0] source0_q, source0_n;
  logic [SOURCE_WIDTH-1:0] source1_q, source1_n;

  logic count_legal;
  logic sources_legal;
  logic sources_live;
  logic offer_fire;
  logic [1:0] retire_count;
  logic [1:0] remaining_count;
  logic link_fit;
  logic [NUM_SOURCES-1:0] accepted_mask;
  logic [EVENT_WIDTH-1:0] accepted_event0;
  logic [EVENT_WIDTH-1:0] accepted_event1;

  always @* begin
    count_legal = k2_count_is_legal(offer_count);
    sources_legal = 1'b1;
    sources_live = 1'b1;
    accepted_event0 = '0;
    accepted_event1 = '0;

    // Guard every dynamic array access so non-power-of-two source counts also
    // fail closed instead of indexing outside the logical source set.
    if (offer_count != 2'd0) begin
      sources_legal = (int'(offer_source0) < NUM_SOURCES);
      if (sources_legal) begin
        sources_live = source_valid[offer_source0];
        accepted_event0 = source_event[offer_source0];
      end else begin
        sources_live = 1'b0;
      end
    end
    if (offer_count == 2'd2) begin
      sources_legal = sources_legal &&
                      (int'(offer_source1) < NUM_SOURCES) &&
                      (offer_source1 != offer_source0);
      if ((int'(offer_source1) < NUM_SOURCES) &&
          (offer_source1 != offer_source0)) begin
        sources_live = sources_live && source_valid[offer_source1];
        accepted_event1 = source_event[offer_source1];
      end else begin
        sources_live = 1'b0;
      end
    end

    // The younger entry is visible only when both buffered entries transfer
    // on this edge.  A ready younger lane can never bypass a blocked head.
    retire_valid = '0;
    retire_event[0] = event0_q;
    retire_event[1] = event1_q;
    retire_source[0] = source0_q;
    retire_source[1] = source1_q;
    if (rst_n) begin
      retire_valid[0] = (count_q != 2'd0);
      retire_valid[1] = (count_q == 2'd2) &&
                        retire_ready[0] && retire_ready[1];
    end

    retire_count = 2'd0;
    if (rst_n && (count_q != 2'd0) && retire_ready[0]) begin
      retire_count = 2'd1;
      if ((count_q == 2'd2) && retire_ready[1])
        retire_count = 2'd2;
    end
    remaining_count = count_q - retire_count;
    link_fit = count_legal &&
               (offer_count <= (2'd2 - remaining_count));

    // offer_ready is the scheduler's sole atomic commit permission.  It is
    // denied during reset, for malformed bundles, when an offered source is
    // not live, or when the complete offer does not fit after this edge's
    // ordered retire transfers.
    offer_ready = rst_n && link_fit && sources_legal && sources_live;
    offer_fire = (offer_count != 2'd0) && offer_ready;

    accepted_mask = '0;
    if (offer_fire) begin
      accepted_mask[offer_source0] = 1'b1;
      if (offer_count == 2'd2)
        accepted_mask[offer_source1] = 1'b1;
    end
    source_ready = rst_n ? accepted_mask : '0;

    count_n = remaining_count;
    event0_n = '0;
    event1_n = '0;
    source0_n = '0;
    source1_n = '0;
    case (retire_count)
      2'd0: begin
        event0_n = event0_q;
        event1_n = event1_q;
        source0_n = source0_q;
        source1_n = source1_q;
      end
      2'd1: begin
        event0_n = event1_q;
        source0_n = source1_q;
      end
      default: begin end
    endcase

    if (offer_fire) begin
      if (remaining_count == 2'd0) begin
        event0_n = accepted_event0;
        source0_n = offer_source0;
        if (offer_count == 2'd2) begin
          event1_n = accepted_event1;
          source1_n = offer_source1;
        end
      end else begin
        // Capacity arithmetic permits only a one-entry offer here.
        event1_n = accepted_event0;
        source1_n = offer_source0;
      end
      count_n = remaining_count + offer_count;
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      count_q <= 2'd0;
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

  always @* begin
    link_empty = (count_q == 2'd0);
    // Reset is externally quiet/idle.  Outside reset, final drain additionally
    // requires empty source latches and a truthful scheduler-idle declaration.
    drain_idle = !rst_n ||
                 (scheduler_idle && link_empty && (source_valid == '0) &&
                  (offer_count == 2'd0));
  end

`ifndef SYNTHESIS
  initial begin
    if (RETIRE_LANES != K2_RETIRE_LANES)
      $fatal(1, "K2_SHIM requires RETIRE_LANES=2");
    if (NUM_SOURCES <= 0)
      $fatal(1, "K2_SHIM requires NUM_SOURCES>0");
    if (EVENT_WIDTH <= 0)
      $fatal(1, "K2_SHIM requires EVENT_WIDTH>0");
    if ((2**SOURCE_WIDTH) < NUM_SOURCES)
      $fatal(1, "K2_SHIM SOURCE_WIDTH cannot encode NUM_SOURCES");
  end

  // Portable immediate protocol assertions.  These intentionally avoid
  // simulator-specific SVA syntax so the same source compiles in Icarus,
  // lint/simulation tools, and the project Xcelium flow.
  always @(posedge clk) begin
    if (!rst_n) begin
      if ((source_ready !== '0) || (retire_valid !== '0) || !link_empty ||
          !drain_idle)
        $fatal(1, "K2_SHIM reset was not quiet and drained");
    end else begin
      if (count_q > 2'd2)
        $fatal(1, "K2_SHIM illegal buffered count=%0d", count_q);
      if (!count_legal && (offer_ready || (source_ready != '0)))
        $fatal(1, "K2_SHIM accepted illegal offer_count=%0d", offer_count);
      if (source_ready !== accepted_mask)
        $fatal(1, "K2_SHIM source_ready is not exact accepted offer mask");
      if ((source_ready & ~source_valid) != '0)
        $fatal(1, "K2_SHIM acknowledged a non-live source");
      if (retire_valid[1] && !retire_valid[0])
        $fatal(1, "K2_SHIM retire lane hole");
      if (retire_valid[1] &&
          !(retire_ready[0] && retire_ready[1] && (count_q == 2'd2)))
        $fatal(1, "K2_SHIM younger entry bypassed ordered transfer");
      if (link_empty !== (count_q == 2'd0))
        $fatal(1, "K2_SHIM link_empty is not truthful");
      if (drain_idle &&
          (!scheduler_idle || !link_empty || (source_valid != '0) ||
           (offer_count != 2'd0) || (retire_valid != '0) ||
           (source_ready != '0)))
        $fatal(1, "K2_SHIM drain_idle asserted with live work");
      if ((remaining_count == 2'd1) && offer_fire &&
          (offer_count != 2'd1))
        $fatal(1, "K2_SHIM accepted a non-fitting offer");
    end
  end
`endif
endmodule
