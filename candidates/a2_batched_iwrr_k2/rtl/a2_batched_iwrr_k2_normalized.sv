`timescale 1ns/1ps

// Frozen N16/K2 normalized wrapper.  The IWRR owner commits only when the
// charged ordered link can absorb its complete offer.  source_ready therefore
// acknowledges exactly the source events captured into that atomic bundle.
module a2_batched_iwrr_k2_normalized #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 16,
  parameter int RETIRE_LANES = 2,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (
  input  logic                         clk,
  input  logic                         rst_n,
  input  logic [NUM_SOURCES-1:0]       source_valid,
  output logic [NUM_SOURCES-1:0]       source_ready,
  input  logic [NUM_SOURCES*ADDR_WIDTH-1:0] source_event,
  output logic [RETIRE_LANES-1:0]      retire_valid,
  input  logic [RETIRE_LANES-1:0]      retire_ready,
  output logic [RETIRE_LANES*ADDR_WIDTH-1:0] retire_event,
  output logic [RETIRE_LANES*SOURCE_WIDTH-1:0] retire_source,
  output logic                         drain_idle
);
  logic [1:0] native_count;
  logic [3:0] native_addr0, native_addr1;
  logic [15:0] native_bitmap;
  // The frozen owner places grant outputs and bundle-ready-controlled next
  // state in one always_comb.  Verilator's process-granularity dependency
  // graph therefore reports a false cycle here; Yosys check resolves the
  // value-level cone and reports no combinational loop.
  /* verilator lint_off UNOPTFLAT */
  logic native_bundle_ready;
  /* verilator lint_on UNOPTFLAT */
  logic native_drain_idle;

  logic link_offer_ready;
  logic [1:0] link_retire_valid;
  logic [ADDR_WIDTH-1:0] link_retire_event0, link_retire_event1;
  logic [SOURCE_WIDTH-1:0] link_retire_source0, link_retire_source1;
  logic link_empty;
  logic bundle_fire;

  assign native_bundle_ready = rst_n && link_offer_ready;
  assign bundle_fire = (native_count != 0) && native_bundle_ready;

  a2_batched_iwrr_k2 owner (
    .clk(clk),
    .rst(!rst_n),
    .req(source_valid),
    .grant_count(native_count),
    .grant_addr0(native_addr0),
    .grant_addr1(native_addr1),
    .grant_bitmap(native_bitmap),
    .bundle_ready(native_bundle_ready),
    .drain_idle(native_drain_idle)
  );

  a2_k2_ordered_link_adapter #(
    .EVENT_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) ordered_link (
    .clk(clk),
    .rst(!rst_n),
    .offer_count(native_count),
    .offer_event0(source_event[native_addr0*ADDR_WIDTH +: ADDR_WIDTH]),
    .offer_event1(source_event[native_addr1*ADDR_WIDTH +: ADDR_WIDTH]),
    .offer_source0(SOURCE_WIDTH'(native_addr0)),
    .offer_source1(SOURCE_WIDTH'(native_addr1)),
    .offer_ready(link_offer_ready),
    .retire_valid(link_retire_valid),
    .retire_event0(link_retire_event0),
    .retire_event1(link_retire_event1),
    .retire_source0(link_retire_source0),
    .retire_source1(link_retire_source1),
    .retire_ready(retire_ready),
    .link_empty(link_empty)
  );

  always_comb begin
    source_ready = '0;
    retire_valid = '0;
    retire_event = '0;
    retire_source = '0;

    if (rst_n) begin
      if (bundle_fire)
        source_ready = native_bitmap;
      retire_valid = link_retire_valid;
      retire_event[0*ADDR_WIDTH +: ADDR_WIDTH] = link_retire_event0;
      retire_event[1*ADDR_WIDTH +: ADDR_WIDTH] = link_retire_event1;
      retire_source[0*SOURCE_WIDTH +: SOURCE_WIDTH] = link_retire_source0;
      retire_source[1*SOURCE_WIDTH +: SOURCE_WIDTH] = link_retire_source1;
    end
  end

  // Reset is externally idle after its active edge.  During operation, both
  // the owner request/hold state and charged transport must be empty.
  assign drain_idle = !rst_n || (native_drain_idle && link_empty);

`ifndef SYNTHESIS
  initial begin
    if (NUM_SOURCES != 16)
      $fatal(1, "A2_K2_NORMALIZED requires NUM_SOURCES=16");
    if (RETIRE_LANES != 2)
      $fatal(1, "A2_K2_NORMALIZED requires RETIRE_LANES=2");
    if (SOURCE_WIDTH != 4)
      $fatal(1, "A2_K2_NORMALIZED requires SOURCE_WIDTH=4");
  end

  always_ff @(posedge clk) begin
    if (rst_n) begin
      if ((source_ready & ~source_valid) != 0)
        $fatal(1, "A2_K2_NORMALIZED ready without valid");
      if ((source_ready != 0) && (source_ready != native_bitmap))
        $fatal(1, "A2_K2_NORMALIZED partial source acceptance");
      if ((native_count == 2) && bundle_fire &&
          !(source_ready[native_addr0] && source_ready[native_addr1]))
        $fatal(1, "A2_K2_NORMALIZED split K2 acceptance");
    end
  end
`endif
endmodule
