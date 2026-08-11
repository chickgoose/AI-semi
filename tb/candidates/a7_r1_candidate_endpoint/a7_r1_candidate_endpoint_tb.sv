`timescale 1ns/1ps

module a7_r1_candidate_endpoint_tb;
  localparam time HALF = 8ns;
  logic ref_clk_i, sample_clk_i, rst_n, event_valid_i;
  logic [3:0] event_addr_i;
  logic ddr_ready, ddr_clk, ddr_valid, ddr_idle;
  logic [1:0] ddr_data;
  logic [3:0] ddr_addr;
  logic par_ready, par_clk, par_valid, par_idle;
  logic [3:0] par_data, par_addr;
  logic ddr_consumer_valid_q, par_consumer_valid_q;
  logic [3:0] ddr_consumer_addr_q, par_consumer_addr_q;
  logic [3:0] expected [0:255];
  integer accepted, ddr_available, par_available;
  integer ddr_retired, par_retired, errors;
  integer ddr_rises, ddr_falls, par_rises;
  bit scoreboard_enable;

  a7_r1_candidate_endpoint dut_ddr (
    .ref_clk_i, .sample_clk_i, .rst_n, .event_valid_i, .event_addr_i,
    .event_ready_o(ddr_ready), .burst_clk_o(ddr_clk),
    .burst_data_o(ddr_data), .retire_addr_o(ddr_addr),
    .retire_valid_o(ddr_valid), .drain_idle_o(ddr_idle));

  a7_r1_parallel_reference_top dut_parallel (
    .ref_clk_i, .sample_clk_i, .rst_n, .event_valid_i, .event_addr_i,
    .event_ready_o(par_ready), .link_strobe_o(par_clk),
    .link_data_o(par_data), .retire_addr_o(par_addr),
    .retire_valid_o(par_valid), .drain_idle_o(par_idle));

  initial begin ref_clk_i = 1'b0; forever #(HALF) ref_clk_i = ~ref_clk_i; end
  initial begin sample_clk_i = 1'b0; #12ns; sample_clk_i = 1'b1;
    forever #(HALF) sample_clk_i = ~sample_clk_i; end

  // The primary sink is an always-ready synchronous consumer. These registers
  // sample the prior-cycle endpoint outputs in the pre-NBA region. The
  // endpoint can therefore make an output available one ref cycle after
  // admission, while actual consumer retirement occurs on the following edge.
  always_ff @(posedge ref_clk_i or negedge rst_n) begin
    if (!rst_n) begin
      ddr_consumer_valid_q <= 1'b0;
      ddr_consumer_addr_q <= '0;
      par_consumer_valid_q <= 1'b0;
      par_consumer_addr_q <= '0;
    end else begin
      ddr_consumer_valid_q <= ddr_valid;
      par_consumer_valid_q <= par_valid;
      if (ddr_valid)
        ddr_consumer_addr_q <= ddr_addr;
      if (par_valid)
        par_consumer_addr_q <= par_addr;
    end
  end

  always @(posedge ref_clk_i) begin
    if (rst_n && scoreboard_enable && event_valid_i && ddr_ready) begin
      if (!par_ready) begin $error("reference ready mismatch"); errors = errors + 1; end
      expected[accepted] = event_addr_i;
      accepted = accepted + 1;
    end
    #1ps;
    if (rst_n && scoreboard_enable) begin
      if (ddr_valid !== par_valid) begin
        $error("retire-valid boundary mismatch DDR=%b parallel=%b", ddr_valid, par_valid);
        errors = errors + 1;
      end
      if (ddr_valid) begin
        if (ddr_available >= accepted || ddr_addr !== expected[ddr_available]) begin
          $error("DDR available mismatch index=%0d got=%h", ddr_available, ddr_addr);
          errors = errors + 1;
        end
        ddr_available = ddr_available + 1;
      end
      if (par_valid) begin
        if (par_available >= accepted || par_addr !== expected[par_available]) begin
          $error("parallel available mismatch index=%0d got=%h", par_available, par_addr);
          errors = errors + 1;
        end
        par_available = par_available + 1;
      end
      if (ddr_consumer_valid_q !== par_consumer_valid_q) begin
        $error("consumer-valid mismatch DDR=%b parallel=%b",
               ddr_consumer_valid_q, par_consumer_valid_q);
        errors = errors + 1;
      end
      if (ddr_consumer_valid_q) begin
        if (ddr_retired >= accepted ||
            ddr_consumer_addr_q !== expected[ddr_retired]) begin
          $error("DDR consumer mismatch index=%0d got=%h",
                 ddr_retired, ddr_consumer_addr_q);
          errors = errors + 1;
        end
        ddr_retired = ddr_retired + 1;
      end
      if (par_consumer_valid_q) begin
        if (par_retired >= accepted ||
            par_consumer_addr_q !== expected[par_retired]) begin
          $error("parallel consumer mismatch index=%0d got=%h",
                 par_retired, par_consumer_addr_q);
          errors = errors + 1;
        end
        par_retired = par_retired + 1;
      end
    end
  end

  always @(posedge ddr_clk) begin
    if (rst_n && scoreboard_enable) begin
      if (ddr_rises >= accepted || ddr_data !== expected[ddr_rises][1:0]) begin
        $error("DDR rise mismatch index=%0d data=%b", ddr_rises, ddr_data);
        errors = errors + 1;
      end
      ddr_rises = ddr_rises + 1;
    end
  end

  always @(negedge ddr_clk) begin
    if (rst_n && scoreboard_enable) begin
      if (ddr_falls >= accepted || ddr_data !== expected[ddr_falls][3:2]) begin
        $error("DDR fall mismatch index=%0d data=%b", ddr_falls, ddr_data);
        errors = errors + 1;
      end
      ddr_falls = ddr_falls + 1;
    end
  end

  always @(posedge par_clk) begin
    if (rst_n && scoreboard_enable) begin
      if (par_rises >= accepted || par_data !== expected[par_rises]) begin
        $error("parallel link mismatch index=%0d data=%h", par_rises, par_data);
        errors = errors + 1;
      end
      par_rises = par_rises + 1;
    end
  end

  task automatic clear_scoreboard;
    begin
      accepted = 0; ddr_available = 0; par_available = 0;
      ddr_retired = 0; par_retired = 0;
      ddr_rises = 0; ddr_falls = 0; par_rises = 0;
    end
  endtask

  task automatic wait_drain;
    integer timeout;
    begin
      timeout = 0;
      while (((ddr_retired != accepted) || (par_retired != accepted) ||
              ddr_consumer_valid_q || par_consumer_valid_q ||
              !ddr_idle || !par_idle) && timeout < 80) begin
        @(posedge ref_clk_i); timeout = timeout + 1;
      end
      if (timeout == 80) begin $error("drain timeout"); errors = errors + 1; end
    end
  endtask

  task automatic legal_reset_idle;
    begin
      event_valid_i = 1'b0;
      wait_drain();
      @(negedge sample_clk_i);
      if (!ddr_idle || !par_idle || ref_clk_i !== 1'b0)
        $fatal(1, "legal reset precondition missing");
      rst_n = 1'b0;
      repeat (2) @(negedge sample_clk_i);
      clear_scoreboard();
      rst_n = 1'b1;
      @(negedge ref_clk_i);
    end
  endtask

  task automatic send_pulse(input logic [3:0] address, input integer gap_cycles);
    integer gap;
    begin
      @(negedge ref_clk_i); event_valid_i = 1'b1; event_addr_i = address;
      @(negedge ref_clk_i); event_valid_i = 1'b0;
      for (gap = 0; gap < gap_cycles; gap = gap + 1) @(negedge ref_clk_i);
    end
  endtask

  task automatic send_continuous(input integer count, input integer salt);
    integer index;
    begin
      @(negedge ref_clk_i);
      event_valid_i = 1'b1;
      for (index = 0; index < count; index = index + 1) begin
        event_addr_i = 4'(((index * 5) + salt) & 15);
        if (index != count-1) @(negedge ref_clk_i);
      end
      @(negedge ref_clk_i); event_valid_i = 1'b0;
    end
  endtask

  initial begin
    rst_n = 1'b0; event_valid_i = 1'b0; event_addr_i = '0;
    errors = 0;
    scoreboard_enable = 1'b1; clear_scoreboard();
    repeat (3) @(negedge sample_clk_i);
    rst_n = 1'b1;
    @(negedge ref_clk_i);

    send_pulse(4'h9, 0);
    wait_drain();
    if (accepted != 1 || ddr_available != 1 || par_available != 1 ||
        ddr_retired != 1 || par_retired != 1)
      $fatal(1, "nominal count mismatch");
    $display("A7_R1_NOMINAL_PASS");

    // At the first ref edge after raw RX commit the registered output is
    // available, but the real consumer has sampled the preceding value. The
    // valid output must keep drain_idle low until the following ref edge.
    clear_scoreboard();
    @(negedge ref_clk_i);
    event_valid_i = 1'b1; event_addr_i = 4'he;
    #2ps;
    if (!ddr_ready || !par_ready || ddr_idle || par_idle)
      $fatal(1, "same-cycle launch request incorrectly reported drain idle");
    $display("A7_R1_SAME_CYCLE_ADMISSION_RESET_BLOCK_PASS");
    @(negedge ref_clk_i); event_valid_i = 1'b0;
    do begin @(posedge ref_clk_i); #2ps; end while (!ddr_valid);
    if (!par_valid || ddr_available != 1 || par_available != 1 ||
        ddr_retired != 0 || par_retired != 0)
      $fatal(1, "availability/consumer latency boundary mismatch");
    if (ddr_idle || par_idle)
      $fatal(1, "pending registered output incorrectly reported drain idle");
    $display("A7_R1_OUTPUT_AVAILABLE_CYCLE1_PASS");
    $display("A7_R1_PENDING_OUTPUT_RESET_BLOCK_PASS");
    @(posedge ref_clk_i); #2ps;
    if (ddr_retired != 1 || par_retired != 1)
      $fatal(1, "consumer did not retire at cycle 2");
    $display("A7_R1_CONSUMER_RETIRE_CYCLE2_PASS");
    legal_reset_idle();

    clear_scoreboard();
    send_continuous(16, 3);
    wait_drain();
    if (accepted != 16 || ddr_available != 16 || par_available != 16 ||
        ddr_retired != 16 || par_retired != 16 ||
        ddr_rises != 16 || ddr_falls != 16 || par_rises != 16)
      $fatal(1, "continuous-valid/back-to-back count mismatch");
    $display("A7_R1_CONTINUOUS_VALID_CHANGING_ADDRESS_PASS events=16");
    $display("A7_R1_BACK_TO_BACK_PASS events=16");

    clear_scoreboard();
    send_pulse(4'h2, 3); send_pulse(4'hf, 1); send_pulse(4'h4, 5);
    wait_drain();
    if (accepted != 3 || ddr_available != 3 || par_available != 3 ||
        ddr_retired != 3 || par_retired != 3)
      $fatal(1, "gapped count mismatch");
    $display("A7_R1_GAPPED_PASS events=3");

    // ready=0 during reset: valid/address are held, then accepted once after
    // legal release without any valid-edge or rearm behavior.
    legal_reset_idle();
    event_valid_i = 1'b1; event_addr_i = 4'hb;
    rst_n = 1'b0;
    repeat (3) begin
      @(posedge ref_clk_i);
      if (ddr_ready || par_ready) $fatal(1, "ready high during reset stall");
      if (event_addr_i !== 4'hb) $fatal(1, "held address changed");
    end
    @(negedge sample_clk_i);
    rst_n = 1'b1;
    @(posedge ref_clk_i);
    #1ps;
    if (!ddr_ready || !par_ready || accepted != 0)
      $fatal(1, "reset-release arming edge contract mismatch");
    $display("A7_R1_RESET_RELEASE_ARMING_PASS");
    @(posedge ref_clk_i);
    @(negedge ref_clk_i); event_valid_i = 1'b0;
    wait_drain();
    if (accepted != 1 || ddr_available != 1 || par_available != 1 ||
        ddr_retired != 1 || par_retired != 1)
      $fatal(1, "stalled-held transaction count mismatch");
    $display("A7_R1_STALLED_HELD_VALID_PASS events=1");

    legal_reset_idle();
    send_pulse(4'h6, 0); wait_drain();
    legal_reset_idle();
    if (ddr_addr !== 0 || par_addr !== 0 || ddr_valid !== 0 || par_valid !== 0)
      $fatal(1, "drain reset did not clear retire epoch");
    send_pulse(4'hd, 0); wait_drain();
    $display("A7_R1_DRAIN_RESET_PASS");

    // Contract-negative reset: assert after the raw rise while a frame is open.
    clear_scoreboard(); scoreboard_enable = 1'b1;
    @(negedge ref_clk_i); event_valid_i = 1'b1; event_addr_i = 4'h7;
    @(posedge ref_clk_i); #1ps; event_valid_i = 1'b0;
    @(posedge ddr_clk);
    if (ddr_idle || par_idle) $fatal(1, "mid-frame reset was incorrectly safe");
    scoreboard_enable = 1'b0;
    #1ns; rst_n = 1'b0;
    #1ps;
    if (ddr_clk !== 0 || par_clk !== 0) $fatal(1, "invalid reset did not force clocks low");
    if (ddr_valid !== 0 || par_valid !== 0)
      $fatal(1, "invalid reset exposed a phantom retirement");
    repeat (2) @(negedge sample_clk_i);
    clear_scoreboard(); rst_n = 1'b1; scoreboard_enable = 1'b1;
    @(negedge ref_clk_i); send_pulse(4'ha, 0); wait_drain();
    if (accepted != 1 || ddr_available != 1 || par_available != 1 ||
        ddr_retired != 1 || par_retired != 1)
      $fatal(1, "post-invalid-reset clean epoch mismatch");
    $display("A7_R1_INVALID_MIDFRAME_RESET_OBSERVED_PASS");

    if (errors != 0) $fatal(1, "A7 R1 endpoint errors=%0d", errors);
    $display("A7_R1_EXACT_ONCE_ORDER_ADDRESS_PASS");
    $display("A7_R1_ENDPOINT_REGRESSION_PASS");
    $finish;
  end
endmodule
