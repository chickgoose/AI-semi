`timescale 1ns/1ps

module a6_w7_smoke_tb;
  localparam integer REF_PERIOD_TICKS = 16;
  localparam integer RESET_ASSERT_TICK = 1;
  localparam integer RESET_RELEASE_TICK = 29;
  localparam integer RESET_RELEASE_PHASE_TICKS = 13;
  localparam integer SAMPLE_RISE_PHASE_TICKS = 4;
  localparam integer SAMPLE_FALL_PHASE_TICKS = 12;

  logic ref_clk_i = 1'b0;
  logic sample_clk_i = 1'b0;
  logic rst_n = 1'b1;
  logic [15:0] source_valid = '0;
  wire [15:0] source_ready;
  wire burst_clk_o;
  wire [1:0] burst_data_o;
  wire link_strobe_o;
  wire [3:0] link_data_o;
  wire [3:0] retire_addr_o;
  wire retire_valid_o;
  wire drain_idle_o;
  wire protocol_fault_o;
  wire observed_link;
`ifdef W7_PARALLEL
  assign observed_link = link_strobe_o;
`else
  assign observed_link = burst_clk_o;
`endif

  integer accepted_count = 0;
  integer retired_count = 0;
  integer queue_head = 0;
  integer queue_tail = 0;
  integer cycle = 0;
  integer contention_start_count = 0;
  integer reset_assert_seen = 0;
  integer reset_active_ref_edges = 0;
  reg [3:0] accepted_queue [0:63];
  reg [15:0] sampled_valid;
  reg [15:0] sampled_ready;

  function automatic integer popcount16(input reg [15:0] value);
    integer index;
    begin
      popcount16 = 0;
      for (index = 0; index < 16; index = index + 1)
        if (value[index] === 1'b1) popcount16 = popcount16 + 1;
    end
  endfunction

  function automatic [3:0] onehot_index16(input reg [15:0] value);
    integer index;
    begin
      onehot_index16 = '0;
      for (index = 0; index < 16; index = index + 1)
        if (value[index] === 1'b1) onehot_index16 = index[3:0];
    end
  endfunction

  initial begin
    #0 ref_clk_i = 1'b1;
    forever #8 ref_clk_i = ~ref_clk_i;
  end
  initial begin
    #4 sample_clk_i = 1'b1;
    forever #8 sample_clk_i = ~sample_clk_i;
  end
  initial begin
    #1 rst_n = 1'b0;
    #28 rst_n = 1'b1;
  end

  always @(negedge rst_n) begin
    reset_assert_seen = 1;
    if ($time != RESET_ASSERT_TICK)
      $fatal(1, "reset assertion must be tick 1: %0t", $time);
  end

  always @(posedge rst_n) begin
    if (!reset_assert_seen)
      $fatal(1, "reset released without an observed assertion edge");
    if ($time != RESET_RELEASE_TICK ||
        ($time % REF_PERIOD_TICKS) != RESET_RELEASE_PHASE_TICKS)
      $fatal(1, "reset release must be tick 29 and phase 13 modulo 16: %0t",
             $time);
    if (reset_active_ref_edges < 1)
      $fatal(1, "reset assertion covered no ref-clock posedge");
    if (ref_clk_i !== 1'b0 || sample_clk_i !== 1'b0)
      $fatal(1, "reset release clocks not both low: ref=%b sample=%b",
             ref_clk_i, sample_clk_i);
    $display("RESET_CONTRACT_PASS assert=1 release=29 phase=13 reset_ref_edges=%0d",
             reset_active_ref_edges);
  end
  always @(posedge ref_clk_i)
    if (($time % REF_PERIOD_TICKS) != 0)
      $fatal(1, "ref rise phase mismatch: %0t", $time);
  always @(posedge sample_clk_i)
    if (($time % REF_PERIOD_TICKS) != SAMPLE_RISE_PHASE_TICKS)
      $fatal(1, "sample rise phase mismatch: %0t", $time);
  always @(negedge sample_clk_i)
    if (($time % REF_PERIOD_TICKS) != SAMPLE_FALL_PHASE_TICKS)
      $fatal(1, "sample fall phase mismatch: %0t", $time);

`ifdef W7_PARALLEL
  a5_owner_semantics_parallel_top dut (
    .ref_clk_i(ref_clk_i), .sample_clk_i(sample_clk_i), .rst_n(rst_n),
    .source_valid(source_valid), .source_ready(source_ready),
    .link_strobe_o(link_strobe_o), .link_data_o(link_data_o),
    .retire_addr_o(retire_addr_o), .retire_valid_o(retire_valid_o),
    .drain_idle_o(drain_idle_o), .protocol_fault_o(protocol_fault_o)
  );
`else
  a7_weighted_fovea_ddr dut (
    .ref_clk_i(ref_clk_i), .sample_clk_i(sample_clk_i), .rst_n(rst_n),
    .source_valid(source_valid), .source_ready(source_ready),
    .burst_clk_o(burst_clk_o), .burst_data_o(burst_data_o),
    .retire_addr_o(retire_addr_o), .retire_valid_o(retire_valid_o),
    .drain_idle_o(drain_idle_o), .protocol_fault_o(protocol_fault_o)
  );
`endif

  // Capture the request/ready handshake in the pre-edge state consumed by the
  // DUT.  Retire and observable-state checks occur 1ps later, after NBA and
  // combinational settling, so neither side depends on active-region order.
  always @(posedge ref_clk_i) begin
    cycle = cycle + 1;
    if (reset_assert_seen && !rst_n)
      reset_active_ref_edges = reset_active_ref_edges + 1;
    if (reset_assert_seen && rst_n) begin
      sampled_valid = source_valid;
      sampled_ready = source_ready;
      if (^sampled_ready === 1'bx)
        $fatal(1, "unknown source_ready before ref edge at cycle %0d", cycle);
      if ((sampled_ready & ~sampled_valid) !== 16'b0)
        $fatal(1, "ready without matching valid at cycle %0d: valid=%h ready=%h",
               cycle, sampled_valid, sampled_ready);

      if (|(sampled_valid & sampled_ready)) begin
        if (popcount16(sampled_valid & sampled_ready) != 1)
          $fatal(1, "non-scalar acceptance at cycle %0d: %h",
                 cycle, sampled_valid & sampled_ready);
        accepted_queue[queue_tail] = onehot_index16(sampled_valid & sampled_ready);
        $display("ACCEPT cycle=%0d addr=%0d", cycle, accepted_queue[queue_tail]);
        queue_tail = queue_tail + 1;
        accepted_count = accepted_count + 1;
      end

      #1ps;
      if (^source_ready === 1'bx)
        $fatal(1, "unknown source_ready after ref edge at cycle %0d", cycle);
      if (drain_idle_o !== 1'b0 && drain_idle_o !== 1'b1)
        $fatal(1, "unknown drain_idle_o after ref edge at cycle %0d", cycle);
      if (observed_link !== 1'b0 && observed_link !== 1'b1)
        $fatal(1, "unknown link after ref edge at cycle %0d", cycle);
      if (protocol_fault_o !== 1'b0)
        $fatal(1, "protocol_fault_o asserted or unknown at cycle %0d", cycle);
      $display("CYCLE cycle=%0d valid=%04h ready=%04h drain=%0b fault=%0b",
               cycle, sampled_valid, sampled_ready, drain_idle_o, protocol_fault_o);

      if (retire_valid_o === 1'b1) begin
        if (queue_head >= queue_tail)
          $fatal(1, "phantom retirement addr=%0d cycle=%0d", retire_addr_o, cycle);
        if (retire_addr_o !== accepted_queue[queue_head])
          $fatal(1, "retirement order mismatch index=%0d expected=%0d actual=%0d",
                 queue_head, accepted_queue[queue_head], retire_addr_o);
        $display("RETIRE cycle=%0d addr=%0d", cycle, retire_addr_o);
        queue_head = queue_head + 1;
        retired_count = retired_count + 1;
      end else if (retire_valid_o !== 1'b0) begin
        $fatal(1, "unknown retire_valid_o at cycle %0d", cycle);
      end
    end
  end

  task automatic emit_edge(input string edge_name);
    begin
      #1ps;
      if (reset_assert_seen && rst_n) begin
        if (^source_ready === 1'bx)
          $fatal(1, "unknown ready at %s edge time %0t", edge_name, $time);
        if (drain_idle_o !== 1'b0 && drain_idle_o !== 1'b1)
          $fatal(1, "unknown drain at %s edge time %0t", edge_name, $time);
        if (observed_link !== 1'b0 && observed_link !== 1'b1)
          $fatal(1, "unknown link at %s edge time %0t", edge_name, $time);
        $display("EDGE edge=%s time=%0t ready=%04h drain=%0b link=%0b valid=%04h",
                 edge_name, $time, source_ready, drain_idle_o, observed_link,
                 source_valid);
      end
    end
  endtask

  // Cycle-exact equivalence includes post-NBA mid-cycle link behavior.
  always @(posedge ref_clk_i)
    emit_edge("ref_pos");
  always @(posedge sample_clk_i)
    emit_edge("sample_pos");
  always @(negedge sample_clk_i)
    emit_edge("sample_neg");
  always @(posedge observed_link)
    emit_edge("link_pos");
  always @(negedge observed_link)
    emit_edge("link_neg");

  // The producer contract is exact ready/valid: hold one occurrence asserted
  // until its matching ready bit is observed, then deassert before the next
  // active edge.  The timeout makes a stuck-ready implementation fail closed.
  task automatic send_one(input integer address);
    integer waited;
    integer accepted_before;
    begin
      @(negedge ref_clk_i);
      source_valid = 16'b1 << address;
      waited = 0;
      accepted_before = accepted_count;
      while (accepted_count == accepted_before) begin
        @(negedge ref_clk_i);
        waited = waited + 1;
        if (waited > 40)
          $fatal(1, "ready timeout for address %0d", address);
      end
      source_valid = '0;
    end
  endtask

  task automatic send_all_contention;
    integer waited;
    integer prior_count;
    integer accepted_addr;
    begin
      @(negedge ref_clk_i);
      source_valid = 16'hffff;
      contention_start_count = accepted_count;
      waited = 0;
      while ((accepted_count - contention_start_count) < 16) begin
        prior_count = accepted_count;
        @(negedge ref_clk_i);
        if (accepted_count != prior_count) begin
          accepted_addr = accepted_queue[queue_tail - 1];
          source_valid[accepted_addr] = 1'b0;
        end
        waited = waited + 1;
        if (waited > 160)
          $fatal(1, "all-16 contention timeout accepted=%0d", accepted_count);
      end
      source_valid = '0;
    end
  endtask

  task automatic wait_for_drain;
    integer waited;
    begin
      waited = 0;
      while (drain_idle_o !== 1'b1) begin
        @(negedge ref_clk_i);
        waited = waited + 1;
        if (waited > 80)
          $fatal(1, "drain timeout accepted=%0d retired=%0d drain=%b",
                 accepted_count, retired_count, drain_idle_o);
      end
    end
  endtask

  initial begin
    wait (rst_n === 1'b0);
    @(posedge rst_n);
    repeat (2) @(negedge ref_clk_i);

    // Exercise every address, then repeat weighted center/periphery addresses
    // so duplicate suppression and stale-address behavior are observable.
    send_one(0);  send_one(5);  send_one(15); send_one(6);
    send_one(9);  send_one(3);  send_one(12); send_one(10);
    send_one(1);  send_one(14); send_one(4);  send_one(11);
    send_one(2);  send_one(13); send_one(7);  send_one(8);
    send_one(5);  send_one(5);  send_one(0);  send_one(15);
    send_all_contention();
    wait_for_drain();
    repeat (3) @(negedge ref_clk_i);

    if (accepted_count !== 36 || retired_count !== 36)
      $fatal(1, "count mismatch expected=36 accepted=%0d retired=%0d",
             accepted_count, retired_count);
    if (queue_head !== queue_tail)
      $fatal(1, "accept/retire imbalance head=%0d tail=%0d", queue_head, queue_tail);
    if (protocol_fault_o !== 1'b0)
      $fatal(1, "protocol fault at clean end");
    if (drain_idle_o !== 1'b1)
      $fatal(1, "drain_idle_o not one at clean end");
    $display("W7_HANDSHAKE_PASS accepted=%0d retired=%0d contention=all16 fault=0 drain=1",
             accepted_count, retired_count);
    $finish;
  end
endmodule
