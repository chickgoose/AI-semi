`timescale 1ns/1ps

module a7_event_triggered_ddr_burst_link_tb;
  localparam int ADDR_WIDTH = 4;
  localparam int DATA_WIDTH = 2;
  localparam int MAX_EVENTS = 512;
  localparam time CORE_HALF_PERIOD = 8ns;
  localparam time MIN_LEGAL_HIGH = 1ns;

  logic core_clk;
  logic ref_clk;
  logic sample_clk;
  logic rst_n;
  logic event_valid;
  logic [ADDR_WIDTH-1:0] event_addr;
  logic event_ready;
  logic tx_burst_clk;
  logic [DATA_WIDTH-1:0] tx_burst_data;
  logic [ADDR_WIDTH-1:0] retire_addr;
  logic retire_toggle;

  logic manual_rst_n;
  logic manual_clk;
  logic [DATA_WIDTH-1:0] manual_data;
  logic [ADDR_WIDTH-1:0] manual_retire_addr;
  logic manual_retire_toggle;

  logic [ADDR_WIDTH-1:0] expected [0:MAX_EVENTS-1];
  integer expected_write;
  integer expected_read;
  integer accepted_count;
  integer retired_count;
  integer rise_count;
  integer fall_count;
  integer current_core_completions;
  integer max_core_completions;
  integer error_count;
  integer merge_edge_count;
  integer merge_gap_errors;
  integer link_ratio;
  integer ref_half_ns;
  integer i;
  logic expected_toggle;
  bit merge_check_active;
  bit merge_edge_seen;
  realtime previous_merge_edge_time;
  string test_name;

  realtime manual_rise_time;
  bit manual_frame_open;
  integer runt_faults;
  integer missing_rise_faults;
  integer missing_fall_faults;

  a7_ddr_burst_tx #(
    .ADDR_WIDTH(ADDR_WIDTH),
    .DATA_WIDTH(DATA_WIDTH)
  ) dut_tx (
    .ref_clk_i(ref_clk),
    .sample_clk_i(sample_clk),
    .rst_n(rst_n),
    .event_valid_i(event_valid),
    .event_addr_i(event_addr),
    .event_ready_o(event_ready),
    .burst_clk_o(tx_burst_clk),
    .burst_data_o(tx_burst_data)
  );

  a7_ddr_burst_rx #(
    .ADDR_WIDTH(ADDR_WIDTH),
    .DATA_WIDTH(DATA_WIDTH)
  ) dut_rx (
    .rst_n(rst_n),
    .burst_clk_i(tx_burst_clk),
    .burst_data_i(tx_burst_data),
    .retire_addr_o(retire_addr),
    .retire_toggle_o(retire_toggle)
  );

  // Fault injection is deliberately outside the production link. It proves
  // that the candidate-only timing checker sees malformed raw burst clocks.
  a7_ddr_burst_rx #(
    .ADDR_WIDTH(ADDR_WIDTH),
    .DATA_WIDTH(DATA_WIDTH)
  ) fault_rx (
    .rst_n(manual_rst_n),
    .burst_clk_i(manual_clk),
    .burst_data_i(manual_data),
    .retire_addr_o(manual_retire_addr),
    .retire_toggle_o(manual_retire_toggle)
  );

  initial begin
    core_clk = 1'b0;
    forever #(CORE_HALF_PERIOD) core_clk = ~core_clk;
  end

  initial begin
    ref_clk = 1'b0;
    wait (ref_half_ns > 0);
    forever #(ref_half_ns * 1ns) ref_clk = ~ref_clk;
  end

  initial begin
    sample_clk = 1'b0;
    wait (ref_half_ns > 0);
    // ref_clk first rises after ref_half_ns. The sample clock rises one
    // quarter reference period later and remains frequency locked.
    #((ref_half_ns + (ref_half_ns / 2)) * 1ns);
    sample_clk = 1'b1;
    forever #(ref_half_ns * 1ns) sample_clk = ~sample_clk;
  end

  always @(posedge ref_clk) begin
    if (rst_n && event_valid && event_ready) begin
      if (expected_write >= MAX_EVENTS)
        $fatal(1, "expected-event storage overflow");
      expected[expected_write] = event_addr;
      expected_write = expected_write + 1;
      accepted_count = accepted_count + 1;
    end
  end

  always @(posedge tx_burst_clk) begin
    if (rst_n) begin
      rise_count = rise_count + 1;
      if (expected_read >= expected_write) begin
        $error("unexpected burst rising edge at %0t", $time);
        error_count = error_count + 1;
      end else if (tx_burst_data !== expected[expected_read][1:0]) begin
        $error("low symbol mismatch index=%0d got=%b expected=%b",
               expected_read, tx_burst_data, expected[expected_read][1:0]);
        error_count = error_count + 1;
      end
    end
  end

  always @(negedge tx_burst_clk) begin
    if (rst_n) begin
      fall_count = fall_count + 1;
      if (expected_read >= expected_write) begin
        $error("unexpected burst falling edge at %0t", $time);
        error_count = error_count + 1;
      end else begin
        if (tx_burst_data !== expected[expected_read][3:2]) begin
          $error("high symbol mismatch index=%0d got=%b expected=%b",
                 expected_read, tx_burst_data, expected[expected_read][3:2]);
          error_count = error_count + 1;
        end
        expected_toggle = ~expected_toggle;
        #1ps;
        if (retire_addr !== expected[expected_read]) begin
          $error("retire address mismatch index=%0d got=%h expected=%h",
                 expected_read, retire_addr, expected[expected_read]);
          error_count = error_count + 1;
        end
        if (retire_toggle !== expected_toggle) begin
          $error("retire toggle mismatch index=%0d got=%b expected=%b",
                 expected_read, retire_toggle, expected_toggle);
          error_count = error_count + 1;
        end
        expected_read = expected_read + 1;
        retired_count = retired_count + 1;
        current_core_completions = current_core_completions + 1;
      end
    end
  end

  always @(posedge core_clk) begin
    if (rst_n) begin
      if (current_core_completions > max_core_completions)
        max_core_completions = current_core_completions;
      current_core_completions = 0;
    end
  end

  always @(tx_burst_clk) begin
    if (rst_n && merge_check_active) begin
      if (merge_edge_seen) begin
        if (($realtime - previous_merge_edge_time) != (ref_half_ns * 1ns))
          merge_gap_errors = merge_gap_errors + 1;
      end else begin
        merge_edge_seen = 1'b1;
      end
      previous_merge_edge_time = $realtime;
      merge_edge_count = merge_edge_count + 1;
    end
  end

  always @(posedge manual_clk) begin
    if (manual_rst_n) begin
      manual_rise_time = $realtime;
      manual_frame_open = 1'b1;
    end
  end

  always @(negedge manual_clk) begin
    if (manual_rst_n) begin
      if (!manual_frame_open) begin
        missing_rise_faults = missing_rise_faults + 1;
      end else begin
        if (($realtime - manual_rise_time) < MIN_LEGAL_HIGH)
          runt_faults = runt_faults + 1;
        manual_frame_open = 1'b0;
      end
    end
  end

  task automatic clear_scoreboard;
    begin
      expected_write = 0;
      expected_read = 0;
      accepted_count = 0;
      retired_count = 0;
      rise_count = 0;
      fall_count = 0;
      current_core_completions = 0;
      max_core_completions = 0;
      expected_toggle = 1'b0;
    end
  endtask

  task automatic reset_link;
    begin
      event_valid = 1'b0;
      event_addr = '0;
      rst_n = 1'b0;
      repeat (3) @(posedge ref_clk);
      clear_scoreboard();
      rst_n = 1'b1;
      @(negedge ref_clk);
    end
  endtask

  task automatic send_back_to_back(input integer count, input integer salt);
    integer index;
    begin
      for (index = 0; index < count; index = index + 1) begin
        @(negedge ref_clk);
        event_valid = 1'b1;
        event_addr = ADDR_WIDTH'(((index * 5) + salt) & 15);
      end
      @(negedge ref_clk);
      event_valid = 1'b0;
      event_addr = '0;
    end
  endtask

  task automatic wait_for_drain(input integer timeout_cycles);
    integer timeout;
    begin
      timeout = 0;
      while ((expected_read != expected_write) &&
             (timeout < timeout_cycles)) begin
        @(posedge ref_clk);
        timeout = timeout + 1;
      end
      if (expected_read != expected_write) begin
        $error("drain timeout accepted=%0d retired=%0d",
               expected_write, expected_read);
        error_count = error_count + 1;
      end
    end
  endtask

  task automatic run_normal;
    integer idle_rises;
    integer idle_falls;
    begin
      reset_link();

      idle_rises = rise_count;
      idle_falls = fall_count;
      repeat (8) @(posedge ref_clk);
      if ((rise_count != idle_rises) || (fall_count != idle_falls)) begin
        $error("idle clock did not stop rises=%0d falls=%0d",
               rise_count-idle_rises, fall_count-idle_falls);
        error_count = error_count + 1;
      end
      $display("A7_DDR_IDLE_STOP_PASS ratio=%0d", link_ratio);

      merge_check_active = 1'b1;
      merge_edge_seen = 1'b0;
      merge_edge_count = 0;
      merge_gap_errors = 0;
      send_back_to_back(16, 0);
      wait_for_drain(8);
      merge_check_active = 1'b0;
      if ((accepted_count != 16) || (retired_count != 16) ||
          (rise_count != 16) || (fall_count != 16)) begin
        $error("back-to-back framing count mismatch a=%0d r=%0d rise=%0d fall=%0d",
               accepted_count, retired_count, rise_count, fall_count);
        error_count = error_count + 1;
      end
      if ((merge_edge_count != 32) || (merge_gap_errors != 0)) begin
        $error("burst merge was not continuous edges=%0d gap_errors=%0d",
               merge_edge_count, merge_gap_errors);
        error_count = error_count + 1;
      end
      $display("A7_DDR_BACK_TO_BACK_PASS ratio=%0d events=16", link_ratio);
      $display("A7_DDR_BURST_MERGE_PASS ratio=%0d edges=32", link_ratio);
      $display("A7_DDR_EDGE_LOCKSTEP_PASS ratio=%0d rise=16 fall=16", link_ratio);

      send_back_to_back(96, 3);
      wait_for_drain(8);
      repeat (2) @(posedge core_clk);
      if (max_core_completions < link_ratio) begin
        $error("frequency-ratio capacity too low ratio=%0d observed=%0d",
               link_ratio, max_core_completions);
        error_count = error_count + 1;
      end
      if (max_core_completions > (link_ratio + 1)) begin
        $error("frequency-ratio accounting too high ratio=%0d observed=%0d",
               link_ratio, max_core_completions);
        error_count = error_count + 1;
      end
      $display("A7_DDR_FREQUENCY_RATIO_PASS ratio=%0d observed_max_per_core=%0d",
               link_ratio, max_core_completions);

      // Reset only after a complete drain is the common contract.
      rst_n = 1'b0;
      #1ps;
      if ((retire_addr !== '0) || (retire_toggle !== 1'b0) ||
          (tx_burst_clk !== 1'b0)) begin
        $error("reset did not clear link state");
        error_count = error_count + 1;
      end
      repeat (2) @(posedge ref_clk);
      clear_scoreboard();
      rst_n = 1'b1;
      @(negedge ref_clk);
      send_back_to_back(8, 11);
      wait_for_drain(8);
      if ((accepted_count != 8) || (retired_count != 8)) begin
        $error("post-reset traffic mismatch accepted=%0d retired=%0d",
               accepted_count, retired_count);
        error_count = error_count + 1;
      end
      $display("A7_DDR_RESET_DRAIN_PASS ratio=%0d", link_ratio);

      if (error_count != 0)
        $fatal(1, "A7 DDR normal regression failed errors=%0d", error_count);
      $display("A7_DDR_NORMAL_PASS ratio=%0d accepted=%0d retired=%0d max_per_core=%0d",
               link_ratio, accepted_count, retired_count,
               max_core_completions);
    end
  endtask

  task automatic reset_manual;
    begin
      manual_rst_n = 1'b0;
      manual_clk = 1'b0;
      manual_data = '0;
      manual_frame_open = 1'b0;
      #2ns;
      manual_rst_n = 1'b1;
      #1ns;
    end
  endtask

  task automatic run_faults;
    begin
      rst_n = 1'b0;
      event_valid = 1'b0;
      reset_manual();

      // Runt high pulse: ideal RTL may still capture it, so the timing checker
      // must independently reject it. This is not physical PVT proof.
      manual_data = 2'b01;
      manual_clk = 1'b1;
      #100ps;
      manual_data = 2'b10;
      manual_clk = 1'b0;
      #1ns;
      if (runt_faults != 1)
        $fatal(1, "runt fault was not detected count=%0d", runt_faults);
      $display("A7_DDR_RUNT_FAULT_FAIL_CLOSED_PASS");

      reset_manual();
      // A raw falling edge without an opening edge must remain visible to the
      // protocol checker; no adapter is allowed to hide it.
      manual_rst_n = 1'b0;
      manual_clk = 1'b1;
      #1ns;
      manual_frame_open = 1'b0;
      manual_rst_n = 1'b1;
      #2ns;
      manual_clk = 1'b0;
      #1ns;
      if (missing_rise_faults != 1)
        $fatal(1, "missing-rise fault was not detected count=%0d",
               missing_rise_faults);
      $display("A7_DDR_MISSING_RISE_FAIL_CLOSED_PASS");

      reset_manual();
      manual_data = 2'b11;
      manual_clk = 1'b1;
      #(4 * ref_half_ns * 1ns);
      if (manual_frame_open) begin
        missing_fall_faults = missing_fall_faults + 1;
        manual_frame_open = 1'b0;
      end
      if (missing_fall_faults != 1)
        $fatal(1, "missing-fall fault was not detected count=%0d",
               missing_fall_faults);
      $display("A7_DDR_MISSING_FALL_FAIL_CLOSED_PASS");

      manual_rst_n = 1'b0;
      manual_clk = 1'b0;
      #1ns;
      $display("A7_DDR_FAULT_REGRESSION_PASS runt=%0d missing_rise=%0d missing_fall=%0d",
               runt_faults, missing_rise_faults, missing_fall_faults);
    end
  endtask

  initial begin
    rst_n = 1'b0;
    event_valid = 1'b0;
    event_addr = '0;
    manual_rst_n = 1'b0;
    manual_clk = 1'b0;
    manual_data = '0;
    clear_scoreboard();
    error_count = 0;
    runt_faults = 0;
    missing_rise_faults = 0;
    missing_fall_faults = 0;
    manual_frame_open = 1'b0;
    merge_check_active = 1'b0;
    merge_edge_seen = 1'b0;
    merge_edge_count = 0;
    merge_gap_errors = 0;

    if (!$value$plusargs("LINK_RATIO=%d", link_ratio))
      link_ratio = 1;
    if ((link_ratio != 1) && (link_ratio != 2) && (link_ratio != 4))
      $fatal(1, "LINK_RATIO must be 1, 2, or 4");
    ref_half_ns = 8 / link_ratio;
    if (!$value$plusargs("TEST=%s", test_name))
      test_name = "normal";

    wait (ref_clk === 1'b0);
    #1ns;
    case (test_name)
      "normal": run_normal();
      "faults": run_faults();
      default: $fatal(1, "unknown TEST=%s", test_name);
    endcase
    #2ns;
    $finish;
  end
endmodule
