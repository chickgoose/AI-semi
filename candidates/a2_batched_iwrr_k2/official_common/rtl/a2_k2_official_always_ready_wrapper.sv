module a2_k2_official_always_ready_wrapper #(
    parameter int NUM_SOURCES = 16,
    parameter int ADDR_WIDTH = 16,
    parameter bit OFFICIAL_ALWAYS_READY = 1'b1
) (
    input  logic                         clk,
    input  logic                         rst_n,
    input  logic [NUM_SOURCES-1:0]       source_valid,
    input  logic [ADDR_WIDTH-1:0]        source_event [NUM_SOURCES],
    output logic [NUM_SOURCES-1:0]       source_ready,
    input  logic [1:0]                   retire_ready,
    output logic [1:0]                   retire_valid,
    output logic [ADDR_WIDTH-1:0]        retire_event [2],
    output logic [3:0]                   retire_source [2],
    output logic                         drain_idle
);
  logic [1:0] core_count;
  logic [3:0] core_addr0;
  logic [3:0] core_addr1;
  logic [15:0] core_bitmap;
  logic core_bundle_ready;
  logic core_drain_idle;
  logic [15:0] expected_bitmap;

`ifndef SYNTHESIS
  initial begin
    if (NUM_SOURCES != 16)
      $fatal(1, "A2_K2_CONFIG_NUM_SOURCES expected=16 got=%0d", NUM_SOURCES);
    if (ADDR_WIDTH != 16)
      $fatal(1, "A2_K2_CONFIG_ADDR_WIDTH expected=16 got=%0d", ADDR_WIDTH);
  end
`endif

  // The owner core already contains the only stall-hold state.  This wrapper
  // adds no queue, skid register, valid register, or event reconstruction.
  a2_batched_iwrr_k2 owner (
      .clk(clk),
      .rst(!rst_n),
      .req(source_valid),
      .grant_count(core_count),
      .grant_addr0(core_addr0),
      .grant_addr1(core_addr1),
      .grant_bitmap(core_bitmap),
      .bundle_ready(core_bundle_ready),
      .drain_idle(core_drain_idle)
  );

  assign core_bundle_ready = &retire_ready;

  always_comb begin
    expected_bitmap = 16'd0;
    if (core_count >= 1)
      expected_bitmap[core_addr0] = 1'b1;
    if (core_count == 2)
      expected_bitmap[core_addr1] = 1'b1;

    source_ready = 16'd0;
    retire_valid = 2'b00;
    retire_event[0] = 16'd0;
    retire_event[1] = 16'd0;
    retire_source[0] = 4'd0;
    retire_source[1] = 4'd0;
    drain_idle = 1'b0;

    if (rst_n) begin
      drain_idle = core_drain_idle;
      if (core_count >= 1) begin
        retire_valid[0] = 1'b1;
        retire_event[0] = source_event[core_addr0];
        retire_source[0] = core_addr0;
      end
      if (core_count == 2) begin
        retire_valid[1] = 1'b1;
        retire_event[1] = source_event[core_addr1];
        retire_source[1] = core_addr1;
      end
      if ((core_count != 0) && core_bundle_ready)
        source_ready = core_bitmap;
    end

`ifdef A2_K2_MUT_SWAP_ORDER
    if (rst_n && (core_count == 2)) begin
      retire_event[0] = {{(ADDR_WIDTH-4){1'b0}}, core_addr1};
      retire_source[0] = core_addr1;
      retire_event[1] = {{(ADDR_WIDTH-4){1'b0}}, core_addr0};
      retire_source[1] = core_addr0;
    end
`endif
`ifdef A2_K2_MUT_DUPLICATE_LANE
    if (rst_n && (core_count == 2)) begin
      retire_event[1] = retire_event[0];
      retire_source[1] = retire_source[0];
    end
`endif
`ifdef A2_K2_MUT_DROP_CREDIT
    if (rst_n && (core_count == 2) && core_bundle_ready)
      source_ready[core_addr1] = 1'b0;
`endif
`ifdef A2_K2_MUT_EVENT_CORRUPT
    if (rst_n && (core_count >= 1))
      retire_event[0] = source_event[core_addr0] ^ 16'h0001;
`endif
`ifdef A2_K2_MUT_RESET_LEAK
    if (!rst_n) begin
      retire_valid[0] = 1'b1;
      retire_event[0] = 16'd0;
      retire_source[0] = 4'd0;
    end
`endif
  end

`ifndef SYNTHESIS
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      if ((source_ready !== 16'd0) || (retire_valid !== 2'b00))
        $fatal(1, "A2_K2_ASSERT_RESET_QUIET source_ready=%0h retire_valid=%0h",
               source_ready, retire_valid);
    end else begin
      if (OFFICIAL_ALWAYS_READY && (retire_ready !== 2'b11))
        $fatal(1, "A2_K2_ASSERT_CAPABILITY_ALWAYS_READY ready=%0b",
               retire_ready);
      if (!OFFICIAL_ALWAYS_READY &&
          (retire_ready !== 2'b00) && (retire_ready !== 2'b11))
        $fatal(1, "A2_K2_ASSERT_CAPABILITY_UNIFORM_ONLY ready=%0b",
               retire_ready);
      if (core_count > 2)
        $fatal(1, "A2_K2_ASSERT_COUNT_RANGE count=%0d", core_count);
      if (core_bitmap !== expected_bitmap)
        $fatal(1, "A2_K2_ASSERT_OWNER_BITMAP count=%0d bitmap=%0h expected=%0h",
               core_count, core_bitmap, expected_bitmap);
      if ((core_count == 2) && (core_addr0 == core_addr1))
        $fatal(1, "A2_K2_ASSERT_OWNER_UNIQUE address=%0d", core_addr0);
      if ((core_count >= 1) && !source_valid[core_addr0])
        $fatal(1, "A2_K2_ASSERT_OWNER_LIVE lane=0 source=%0d", core_addr0);
      if ((core_count == 2) && !source_valid[core_addr1])
        $fatal(1, "A2_K2_ASSERT_OWNER_LIVE lane=1 source=%0d", core_addr1);
      if (retire_valid !== ((core_count == 2) ? 2'b11 :
                            (core_count == 1) ? 2'b01 : 2'b00))
        $fatal(1, "A2_K2_ASSERT_VALID_COUNT count=%0d valid=%0b",
               core_count, retire_valid);
      if ((core_count >= 1) &&
          ((retire_source[0] !== core_addr0) ||
           (retire_event[0] !== source_event[core_addr0])))
        $fatal(1, "A2_K2_ASSERT_ORDER lane=0 got=%0d expected=%0d",
               retire_source[0], core_addr0);
      if ((core_count == 2) &&
          ((retire_source[1] !== core_addr1) ||
           (retire_event[1] !== source_event[core_addr1])))
        $fatal(1, "A2_K2_ASSERT_ORDER lane=1 got=%0d expected=%0d",
               retire_source[1], core_addr1);
      if ((core_count == 2) &&
          (retire_source[0] == retire_source[1]))
        $fatal(1, "A2_K2_ASSERT_UNIQUE source=%0d", retire_source[0]);
      if (source_ready !==
          (((core_count != 0) && core_bundle_ready) ? core_bitmap : 16'd0))
        $fatal(1, "A2_K2_ASSERT_ATOMIC_CREDIT count=%0d ready=%0h expected=%0h",
               core_count, source_ready,
               (((core_count != 0) && core_bundle_ready) ?
                core_bitmap : 16'd0));
      if ((source_valid == 16'd0) &&
          ((core_count != 0) || !core_drain_idle))
        $fatal(1, "A2_K2_ASSERT_DRAIN count=%0d drain_idle=%0b",
               core_count, core_drain_idle);
    end
  end
`endif
endmodule
