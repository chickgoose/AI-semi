`timescale 1ns/1ps

// Native common-boundary wrapper for the exact scalar-prefix K2 owner.  The
// scheduler offer remains atomic: source_ready is asserted for exactly all
// addresses in a fitting registered offer, and its policy commits on that same
// edge.  Independent common retire stalls affect only the charged adapter.
module a3_k2_common_wrapper #(
  parameter int NUM_SOURCES  = 16,
  parameter int ADDR_WIDTH   = 16,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (
  input  logic                      clk,
  input  logic                      rst_n,
  input  logic [NUM_SOURCES-1:0]    source_valid,
  output logic [NUM_SOURCES-1:0]    source_ready,
  input  logic [ADDR_WIDTH-1:0]     source_event [NUM_SOURCES],
  output logic [1:0]                retire_valid,
  input  logic [1:0]                retire_ready,
  output logic [ADDR_WIDTH-1:0]     retire_event [2],
  output logic [SOURCE_WIDTH-1:0]   retire_source [2]
);
  logic native_rst;
  logic [1:0] owner_count;
  logic [3:0] owner_addr0;
  logic [3:0] owner_addr1;
  logic owner_bundle_ready;
  logic owner_offer_live;
  logic owner_accept;

  logic [1:0] link_offer_count;
  logic link_offer_ready;
  logic [1:0] link_retire_valid;
  logic [SOURCE_WIDTH-1:0] link_retire_source0;
  logic [SOURCE_WIDTH-1:0] link_retire_source1;
  logic [ADDR_WIDTH-1:0] link_retire_event0;
  logic [ADDR_WIDTH-1:0] link_retire_event1;
  logic link_empty;

  assign native_rst = ~rst_n;

  a3_exact_scalar_prefix_k2 owner (
    .clk(clk),
    .rst(native_rst),
    .source_pending(source_valid),
    .grant_count(owner_count),
    .lane0_addr(owner_addr0),
    .lane1_addr(owner_addr1),
    .bundle_ready(owner_bundle_ready)
  );

  always @* begin
    owner_offer_live = (owner_count != 2'd0) && source_valid[owner_addr0];
    if (owner_count == 2'd2)
      owner_offer_live = owner_offer_live && source_valid[owner_addr1];
  end

  // Suppress the adapter offer unless the whole registered owner bundle is
  // live.  This keeps a count=2 offer indivisible even under bad upstream
  // behavior and makes source_ready exactly match the accepted bundle.
  assign link_offer_count =
    (rst_n && owner_offer_live) ? owner_count : 2'd0;
  assign owner_accept = rst_n && owner_offer_live && link_offer_ready;
  assign owner_bundle_ready = owner_accept;

  always @* begin
    source_ready = '0;
    if (owner_accept) begin
      source_ready[owner_addr0] = 1'b1;
      if (owner_count == 2'd2)
        source_ready[owner_addr1] = 1'b1;
    end
  end

  always @* begin
    retire_valid = rst_n ? link_retire_valid : 2'b00;
    retire_event[0] = link_retire_event0;
    retire_event[1] = link_retire_event1;
    retire_source[0] = link_retire_source0;
    retire_source[1] = link_retire_source1;
  end

  a3_k2_ordered_2entry_adapter #(
    .ADDR_WIDTH(ADDR_WIDTH),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) ordered_link (
    .clk(clk),
    .rst(native_rst),
    .offer_count(link_offer_count),
    .offer_source0(SOURCE_WIDTH'(owner_addr0)),
    .offer_source1(SOURCE_WIDTH'(owner_addr1)),
    .offer_event0(source_event[owner_addr0]),
    .offer_event1(source_event[owner_addr1]),
    .offer_ready(link_offer_ready),
    .retire_valid(link_retire_valid),
    .retire_source0(link_retire_source0),
    .retire_source1(link_retire_source1),
    .retire_event0(link_retire_event0),
    .retire_event1(link_retire_event1),
    .retire_ready(retire_ready),
    .empty(link_empty)
  );

`ifndef SYNTHESIS
  initial begin
    if (NUM_SOURCES != 16)
      $fatal(1, "A3_K2_COMMON requires NUM_SOURCES=16");
    if (SOURCE_WIDTH < 4)
      $fatal(1, "A3_K2_COMMON requires SOURCE_WIDTH>=4");
    if (ADDR_WIDTH <= 0)
      $fatal(1, "A3_K2_COMMON requires ADDR_WIDTH>0");
  end

  always @(posedge clk) begin
    if (rst_n) begin
      if (link_empty && (link_retire_valid != 2'b00))
        $fatal(1, "A3_K2_COMMON empty adapter exposed a retirement");
      if ((owner_count == 2'd2) && owner_accept &&
          (source_ready != ((16'b1 << owner_addr0) |
                            (16'b1 << owner_addr1))))
        $fatal(1, "A3_K2_COMMON partial count=2 source acceptance");
      if ((owner_count == 2'd1) && owner_accept &&
          (source_ready != (16'b1 << owner_addr0)))
        $fatal(1, "A3_K2_COMMON incorrect count=1 source acceptance");
    end
  end
`endif

endmodule
