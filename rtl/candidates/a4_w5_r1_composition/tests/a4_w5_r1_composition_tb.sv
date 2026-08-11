`timescale 1ns/1ps

module a4_w5_r1_composition_tb;
  localparam int MAX_EVENTS = 512;
  localparam time SINK_DELAY = 32ns;
  localparam time SAFE_RELEASE_TO_REF = 4ns;

  logic ref_clk, sample_clk, rst_n;
  logic producer_valid, producer_ready, accepted;
  logic [3:0] producer_addr;
  logic burst_clk;
  logic [1:0] burst_data;
  logic [3:0] serial_addr, parallel_link_data, parallel_addr;
  logic serial_valid, serial_idle, parallel_strobe, parallel_valid, parallel_idle;
  logic sink_valid;
  logic [3:0] sink_serial_addr, sink_parallel_addr;

  integer expected_id [MAX_EVENTS];
  logic [3:0] expected_addr [MAX_EVENTS];
  realtime expected_time [MAX_EVENTS];
  integer expected_write, consumer_read, serial_rise_read, serial_fall_read, parallel_link_read;
  integer next_id, accepted_total, retired_total, reset_aborted;
  integer continuous_accepted, initial_gapped_accepted, all_gapped_accepted;
  integer held_accepted, post_reset_accepted, midframe_aborted;
  integer legal_release_checks;
  realtime reset_release_time;
  bit release_phase_pending;
  bit scoreboard_initialized;

  a4_w5_r1_composition dut (
    .ref_clk_i(ref_clk), .sample_clk_i(sample_clk), .rst_n,
    .producer_valid_i(producer_valid), .producer_addr_i(producer_addr),
    .producer_ready_o(producer_ready), .accepted_o(accepted),
    .burst_clk_o(burst_clk), .burst_data_o(burst_data),
    .serial_retire_addr_o(serial_addr), .serial_retire_valid_o(serial_valid),
    .serial_drain_idle_o(serial_idle), .parallel_strobe_o(parallel_strobe),
    .parallel_link_data_o(parallel_link_data),
    .parallel_retire_addr_o(parallel_addr),
    .parallel_retire_valid_o(parallel_valid), .parallel_drain_idle_o(parallel_idle)
  );

  // A real same-clock sequential consumer samples the endpoint's registered
  // retire outputs in the pre-NBA region. It does not peek at newly-produced
  // retire_valid after the same edge.
  always_ff @(posedge ref_clk or negedge rst_n) begin
    if (!rst_n) begin
      sink_valid <= 1'b0;
      sink_serial_addr <= '0;
      sink_parallel_addr <= '0;
    end else begin
      sink_valid <= serial_valid;
      if (serial_valid) begin
        sink_serial_addr <= serial_addr;
        sink_parallel_addr <= parallel_addr;
      end
    end
  end

  initial begin ref_clk = 1'b0; forever #8ns ref_clk = ~ref_clk; end
  initial begin sample_clk = 1'b0; #12ns sample_clk = 1'b1;
    forever #8ns sample_clk = ~sample_clk; end

  always @(posedge rst_n) begin
    if (sample_clk !== 1'b0)
      $fatal(1, "RESET_RELEASE_PHASE_FAIL sample clock not low at release");
    reset_release_time = $realtime;
    release_phase_pending = 1'b1;
  end

  // IDs live only in this independent scoreboard; link semantics are address-only.
  always @(posedge ref_clk) begin : accept_and_observe
    realtime edge_time;
    edge_time = $realtime;
    if (release_phase_pending) begin
      if ((edge_time - reset_release_time) != SAFE_RELEASE_TO_REF)
        $fatal(1, "RESET_RELEASE_PHASE_FAIL release_to_ref=%0t expected=%0t",
          edge_time-reset_release_time, SAFE_RELEASE_TO_REF);
      release_phase_pending = 1'b0;
      legal_release_checks = legal_release_checks + 1;
    end
    if (rst_n && producer_valid && producer_ready) begin
      if (!accepted || expected_write >= MAX_EVENTS) $fatal(1, "bad admission");
      expected_id[expected_write] = next_id;
      expected_addr[expected_write] = producer_addr;
      expected_time[expected_write] = edge_time;
      expected_write = expected_write + 1;
      next_id = next_id + 1;
      accepted_total = accepted_total + 1;
    end else if (accepted) $fatal(1, "accept without ready-valid handshake");

    // drain_idle is an externally actionable reset permission. It must remain
    // false for both a current launch and a pending registered retirement.
    if (rst_n && ((accepted || serial_valid) && serial_idle))
      $fatal(1, "serial drain_idle high with launch/pending retirement");
    if (rst_n && ((accepted || parallel_valid) && parallel_idle))
      $fatal(1, "parallel drain_idle high with launch/pending retirement");

    #1ps;
    if (rst_n) begin
      if (serial_valid !== parallel_valid)
        $fatal(1, "consumer-boundary valid mismatch");
      if (sink_valid) begin
        if (consumer_read >= expected_write) $fatal(1, "phantom retirement");
        if (sink_serial_addr !== expected_addr[consumer_read] ||
            sink_parallel_addr !== expected_addr[consumer_read])
          $fatal(1, "retire address/order mismatch id=%0d", expected_id[consumer_read]);
        if ((edge_time - expected_time[consumer_read]) != SINK_DELAY)
          $fatal(1, "retire timing mismatch id=%0d delta=%0t",
            expected_id[consumer_read], edge_time-expected_time[consumer_read]);
        consumer_read = consumer_read + 1;
        retired_total = retired_total + 1;
      end
    end
  end

  always @(posedge burst_clk) if (rst_n) begin
    if (serial_rise_read >= expected_write ||
        burst_data !== expected_addr[serial_rise_read][1:0])
      $fatal(1, "DDR low symbol mismatch");
    serial_rise_read = serial_rise_read + 1;
  end

  always @(negedge burst_clk) if (rst_n) begin
    if (serial_fall_read >= expected_write ||
        burst_data !== expected_addr[serial_fall_read][3:2])
      $fatal(1, "DDR high symbol/fall commit mismatch");
    serial_fall_read = serial_fall_read + 1;
  end

  always @(posedge parallel_strobe) if (rst_n) begin
    if (parallel_link_read >= expected_write ||
        parallel_link_data !== expected_addr[parallel_link_read])
      $fatal(1, "parallel link mismatch");
    parallel_link_read = parallel_link_read + 1;
  end

  always @(negedge rst_n) if (scoreboard_initialized) begin
    reset_aborted = reset_aborted + (expected_write - consumer_read);
    consumer_read = expected_write;
    serial_rise_read = expected_write;
    serial_fall_read = expected_write;
    parallel_link_read = expected_write;
  end

  task automatic wait_for_drain(input integer timeout_cycles);
    integer timeout;
    begin
      timeout = 0;
      while ((consumer_read != expected_write || !serial_idle || !parallel_idle) &&
             timeout < timeout_cycles) begin
        @(posedge ref_clk); #1ps; timeout = timeout + 1;
      end
      if (consumer_read != expected_write || !serial_idle || !parallel_idle)
        $fatal(1, "drain timeout");
    end
  endtask

  task automatic send_continuous(input integer count, input integer salt);
    integer accepted_before;
    begin
      accepted_before = accepted_total;
      @(negedge ref_clk); producer_valid = 1'b1;
      for (int index = 0; index < count; index++) begin
        producer_addr = 4'(((index * 5) + salt) & 15);
        if (index != count-1) @(negedge ref_clk);
      end
      @(negedge ref_clk); producer_valid = 1'b0; producer_addr = '0;
      continuous_accepted += accepted_total - accepted_before;
      if (accepted_total - accepted_before != count) $fatal(1, "continuous loss/bubble");
    end
  endtask

  task automatic send_gapped(input integer count, input integer salt);
    integer accepted_before;
    begin
      accepted_before = accepted_total;
      for (int index = 0; index < count; index++) begin
        @(negedge ref_clk); producer_valid = 1'b1;
        producer_addr = 4'(((index * 3) + salt) & 15);
        @(negedge ref_clk); producer_valid = 1'b0;
        repeat ((index % 3) + 1) @(negedge ref_clk);
      end
      all_gapped_accepted += accepted_total - accepted_before;
      if (accepted_total - accepted_before != count) $fatal(1, "gapped loss");
    end
  endtask

  task automatic legal_reset_with_held_valid(input logic [3:0] address);
    integer accepted_before;
    begin
      wait_for_drain(32);
      accepted_before = accepted_total;
      @(negedge sample_clk);
      rst_n = 1'b0; producer_valid = 1'b1; producer_addr = address;
      repeat (3) begin
        @(posedge ref_clk); #1ps;
        if (producer_ready || accepted_total != accepted_before || producer_addr != address)
          $fatal(1, "held-valid reset stall failure");
      end
      @(negedge sample_clk); rst_n = 1'b1; // SAFE_RELEASE_EXACT_4NS
      @(posedge ref_clk); #1ps;
      if (!producer_ready || accepted_total != accepted_before)
        $fatal(1, "charged reset-release arming edge failure");
      @(posedge ref_clk); #1ps;
      if (accepted_total != accepted_before + 1) $fatal(1, "held event not accepted once");
      @(negedge ref_clk); producer_valid = 1'b0; producer_addr = '0;
      held_accepted += 1;
      wait_for_drain(32);
    end
  endtask

  task automatic reset_after_drain;
    integer accepted_before;
    begin
      wait_for_drain(32); accepted_before = accepted_total;
      @(negedge sample_clk); rst_n = 1'b0;
      repeat (2) @(negedge sample_clk);
      rst_n = 1'b1; // SAFE_RELEASE_EXACT_4NS
      @(posedge ref_clk); // charged arming edge
      send_gapped(4, 9);
      post_reset_accepted = accepted_total - accepted_before;
      wait_for_drain(32);
    end
  endtask

  task automatic reset_mid_frame_fail_closed;
    integer accepted_before, retired_before, abort_before;
    begin
      wait_for_drain(32);
      accepted_before = accepted_total; retired_before = retired_total;
      abort_before = reset_aborted;
      @(negedge ref_clk); producer_valid = 1'b1; producer_addr = 4'hd;
      @(posedge ref_clk); #1ps;
      if (accepted_total != accepted_before + 1 || serial_idle || parallel_idle)
        $fatal(1, "mid-frame setup missing");
      @(posedge burst_clk); #1ns;
      rst_n = 1'b0; producer_valid = 1'b0; producer_addr = '0;
      #1ps;
      if (retired_total != retired_before || reset_aborted != abort_before + 1 ||
          burst_clk || parallel_strobe || serial_valid || parallel_valid)
        $fatal(1, "invalid mid-frame reset did not fail closed");
      repeat (2) @(negedge sample_clk);
      rst_n = 1'b1; // SAFE_RELEASE_EXACT_4NS
      @(posedge ref_clk); // charged arming edge
      midframe_aborted += 1;
      send_gapped(1, 6); wait_for_drain(32);
      if (retired_total != retired_before + 1) $fatal(1, "post-invalid-reset epoch failure");
    end
  endtask

  initial begin
    rst_n = 0; producer_valid = 0; producer_addr = 0;
    expected_write = 0; consumer_read = 0; serial_rise_read = 0;
    serial_fall_read = 0; parallel_link_read = 0; next_id = 0;
    accepted_total = 0; retired_total = 0; reset_aborted = 0;
    continuous_accepted = 0; initial_gapped_accepted = 0; all_gapped_accepted = 0;
    held_accepted = 0; post_reset_accepted = 0; midframe_aborted = 0;
    legal_release_checks = 0; reset_release_time = 0; release_phase_pending = 0;
    scoreboard_initialized = 1;

    repeat (2) @(negedge sample_clk);
    rst_n = 1'b1; // SAFE_RELEASE_EXACT_4NS
    @(posedge ref_clk); // charged arming edge
    send_continuous(32, 1); wait_for_drain(32);
    begin integer accepted_before;
      accepted_before = accepted_total; send_gapped(12, 4);
      initial_gapped_accepted = accepted_total - accepted_before;
    end
    wait_for_drain(48);
    legal_reset_with_held_valid(4'ha);
    reset_after_drain();
    reset_mid_frame_fail_closed();

    if (accepted_total != retired_total + reset_aborted) $fatal(1, "conservation failure");
    if (consumer_read != expected_write) $fatal(1, "final order/drain failure");
    if (continuous_accepted != 32 || initial_gapped_accepted != 12 ||
        all_gapped_accepted != 17 || held_accepted != 1 ||
        post_reset_accepted != 4 || midframe_aborted != 1 ||
        legal_release_checks != 4)
      $fatal(1, "scenario counts mismatch");
    $display("A4_W5_R1_COMPOSITION_PASS accepted=%0d retired=%0d aborted=%0d continuous=%0d initial_gapped=%0d all_gapped=%0d held=%0d post_reset=%0d endpoint_valid_ns=16 sink_sample_ns=32 release_phase_ns=4 phase_checks=%0d",
      accepted_total, retired_total, reset_aborted, continuous_accepted,
      initial_gapped_accepted, all_gapped_accepted, held_accepted,
      post_reset_accepted, legal_release_checks);
    $finish;
  end
endmodule
