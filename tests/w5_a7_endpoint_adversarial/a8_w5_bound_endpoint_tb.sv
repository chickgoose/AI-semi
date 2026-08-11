`timescale 1ns/1ps

module a8_w5_bound_endpoint_tb;
  localparam time HALF = 8ns;
  logic ref_clk_i, sample_clk_i, rst_n, event_valid_i;
  logic [3:0] event_addr_i;
  logic ddr_ready, ddr_clk, ddr_retire_valid, ddr_idle;
  logic [1:0] ddr_data;
  logic [3:0] ddr_addr;
  logic par_ready, par_clk, par_retire_valid, par_idle;
  logic [3:0] par_data, par_addr;
  logic [3:0] expected [0:511];
  integer admitted_cycle [0:511];
  integer ref_cycle;
  integer accepted, ddr_available, par_available;
  integer ddr_observed, par_observed;
  integer ddr_rises, ddr_falls, par_frames, errors;

  a7_r1_candidate_endpoint production (
    .ref_clk_i, .sample_clk_i, .rst_n, .event_valid_i, .event_addr_i,
    .event_ready_o(ddr_ready), .burst_clk_o(ddr_clk),
    .burst_data_o(ddr_data), .retire_addr_o(ddr_addr),
    .retire_valid_o(ddr_retire_valid), .drain_idle_o(ddr_idle));

  a7_r1_parallel_reference_top parallel_reference (
    .ref_clk_i, .sample_clk_i, .rst_n, .event_valid_i, .event_addr_i,
    .event_ready_o(par_ready), .link_strobe_o(par_clk),
    .link_data_o(par_data), .retire_addr_o(par_addr),
    .retire_valid_o(par_retire_valid), .drain_idle_o(par_idle));

  initial begin ref_clk_i = 1'b0; forever #(HALF) ref_clk_i = ~ref_clk_i; end
  initial begin sample_clk_i = 1'b0; #12ns; sample_clk_i = 1'b1;
    forever #(HALF) sample_clk_i = ~sample_clk_i; end

  always @(posedge ref_clk_i) begin
    ref_cycle = ref_cycle + 1;
    if (rst_n && (ddr_ready !== par_ready)) begin
      $error("ready mismatch production=%b parallel=%b", ddr_ready, par_ready);
      errors = errors + 1;
    end
    if (rst_n && event_valid_i && ddr_ready) begin
      expected[accepted] = event_addr_i;
      admitted_cycle[accepted] = ref_cycle;
      accepted = accepted + 1;
    end
    // A real always_ff sink sees the producer's registered retire_valid/address
    // values from before this edge. It does not sample post-NBA availability.
    if (rst_n && (ddr_retire_valid !== par_retire_valid)) begin
      $error("observer-valid mismatch production=%b parallel=%b",
             ddr_retire_valid, par_retire_valid);
      errors = errors + 1;
    end
    if (rst_n && ddr_retire_valid) begin
      if (ddr_observed < accepted &&
          ref_cycle - admitted_cycle[ddr_observed] != 2) begin
        $error("production sink latency mismatch index=%0d admission=%0d sink=%0d",
               ddr_observed, admitted_cycle[ddr_observed], ref_cycle);
        errors = errors + 1;
      end
      if (ddr_observed >= accepted || ddr_addr !== expected[ddr_observed]) begin
        $error("production observer mismatch index=%0d got=%h", ddr_observed, ddr_addr);
        errors = errors + 1;
      end
      if (par_addr !== ddr_addr) begin
        $error("parallel observer address mismatch production=%h parallel=%h",
               ddr_addr, par_addr);
        errors = errors + 1;
      end
      ddr_observed = ddr_observed + 1;
    end
    if (rst_n && par_retire_valid) begin
      if (par_observed < accepted &&
          ref_cycle - admitted_cycle[par_observed] != 2) begin
        $error("parallel sink latency mismatch index=%0d admission=%0d sink=%0d",
               par_observed, admitted_cycle[par_observed], ref_cycle);
        errors = errors + 1;
      end
      if (par_observed >= accepted || par_addr !== expected[par_observed]) begin
        $error("parallel observer mismatch index=%0d got=%h", par_observed, par_addr);
        errors = errors + 1;
      end
      par_observed = par_observed + 1;
    end
    if (rst_n && ddr_idle &&
        (ddr_retire_valid || (event_valid_i && ddr_ready))) begin
      $error("production drain_idle hid pending output/launch");
      errors = errors + 1;
    end
    if (rst_n && par_idle &&
        (par_retire_valid || (event_valid_i && par_ready))) begin
      $error("parallel drain_idle hid pending output/launch");
      errors = errors + 1;
    end
  end

  // The producer updates its registered output in the NBA region one ref edge
  // before a real always_ff sink can consume it. Observe that availability
  // boundary separately; this block is intentionally not the sink model.
  always @(posedge ref_clk_i) begin
    #1ps;
    if (rst_n && (ddr_retire_valid !== par_retire_valid)) begin
      $error("availability-valid mismatch production=%b parallel=%b",
             ddr_retire_valid, par_retire_valid);
      errors = errors + 1;
    end
    if (rst_n && ddr_retire_valid) begin
      if (ddr_available >= accepted ||
          ref_cycle - admitted_cycle[ddr_available] != 1) begin
        $error("production availability latency mismatch index=%0d admission=%0d available=%0d",
               ddr_available, admitted_cycle[ddr_available], ref_cycle);
        errors = errors + 1;
      end
      if (ddr_addr !== expected[ddr_available]) begin
        $error("production availability address mismatch index=%0d got=%h",
               ddr_available, ddr_addr);
        errors = errors + 1;
      end
      ddr_available = ddr_available + 1;
    end
    if (rst_n && par_retire_valid) begin
      if (par_available >= accepted ||
          ref_cycle - admitted_cycle[par_available] != 1) begin
        $error("parallel availability latency mismatch index=%0d admission=%0d available=%0d",
               par_available, admitted_cycle[par_available], ref_cycle);
        errors = errors + 1;
      end
      if (par_addr !== expected[par_available]) begin
        $error("parallel availability address mismatch index=%0d got=%h",
               par_available, par_addr);
        errors = errors + 1;
      end
      par_available = par_available + 1;
    end
    if (rst_n && ddr_idle && ddr_retire_valid) begin
      $error("production drain_idle hid post-NBA available output");
      errors = errors + 1;
    end
    if (rst_n && par_idle && par_retire_valid) begin
      $error("parallel drain_idle hid post-NBA available output");
      errors = errors + 1;
    end
  end

  always @(posedge ddr_clk) begin
    if (rst_n) begin
      if (ddr_rises >= accepted || ddr_data !== expected[ddr_rises][1:0]) begin
        $error("production rise/low-half mismatch index=%0d", ddr_rises);
        errors = errors + 1;
      end
      ddr_rises = ddr_rises + 1;
    end
  end

  always @(negedge ddr_clk) begin
    if (rst_n) begin
      if (ddr_falls >= accepted || ddr_data !== expected[ddr_falls][3:2]) begin
        $error("production fall/high-half mismatch index=%0d", ddr_falls);
        errors = errors + 1;
      end
      ddr_falls = ddr_falls + 1;
    end
  end

  always @(posedge par_clk) begin
    if (rst_n) begin
      if (par_frames >= accepted || par_data !== expected[par_frames]) begin
        $error("parallel link mismatch index=%0d", par_frames);
        errors = errors + 1;
      end
      par_frames = par_frames + 1;
    end
  end

  task automatic wait_drain;
    integer timeout;
    begin
      timeout = 0;
      while ((ddr_available != accepted || par_available != accepted ||
              ddr_observed != accepted || par_observed != accepted ||
              !ddr_idle || !par_idle) && timeout < 200) begin
        @(posedge ref_clk_i); timeout = timeout + 1;
      end
      if (timeout == 200) $fatal(1, "drain timeout accepted=%0d ddr=%0d par=%0d",
                                accepted, ddr_observed, par_observed);
    end
  endtask

  task automatic clear_epoch;
    begin
      if (rst_n) $fatal(1, "scoreboard epoch may clear only under reset");
      accepted = 0;
      ddr_available = 0;
      par_available = 0;
      ddr_observed = 0;
      par_observed = 0;
      ddr_rises = 0;
      ddr_falls = 0;
      par_frames = 0;
    end
  endtask

  task automatic legal_reset;
    integer accepted_before;
    begin
      event_valid_i = 1'b0;
      wait_drain();
      @(negedge sample_clk_i);
      if (!ddr_idle || !par_idle || ref_clk_i !== 1'b0)
        $fatal(1, "legal reset precondition missing");
      rst_n = 1'b0;
      repeat (2) @(negedge sample_clk_i);
      rst_n = 1'b1;
      accepted_before = accepted;
      @(posedge ref_clk_i); #1ps;
      if (accepted != accepted_before)
        $fatal(1, "reset-release arming edge falsely handshook");
      if (!ddr_ready || !par_ready)
        $fatal(1, "ready did not arm after first safe ref edge");
      @(negedge ref_clk_i);
    end
  endtask

  task automatic send_continuous(input integer count, input integer salt);
    integer index;
    begin
      event_valid_i = 1'b1;
      for (index = 0; index < count; index = index + 1) begin
        event_addr_i = 4'(((index * 5) + salt) & 15);
        @(negedge ref_clk_i);
      end
      event_valid_i = 1'b0;
    end
  endtask

  initial begin
    integer accepted_before;
    ref_clk_i = 1'b0; sample_clk_i = 1'b0;
    rst_n = 1'b0; event_valid_i = 1'b0; event_addr_i = '0;
    ref_cycle = 0;
    accepted = 0; ddr_available = 0; par_available = 0;
    ddr_observed = 0; par_observed = 0;
    ddr_rises = 0; ddr_falls = 0; par_frames = 0; errors = 0;
    repeat (3) @(negedge sample_clk_i);
    rst_n = 1'b1;
    accepted_before = accepted;
    @(posedge ref_clk_i); #1ps;
    if (accepted != accepted_before) $fatal(1, "initial arming edge handshook");
    if (!ddr_ready || !par_ready) $fatal(1, "initial ready did not arm");
    @(negedge ref_clk_i);

    send_continuous(64, 3);
    wait_drain();
    if (accepted != 64 || ddr_available != 64 || par_available != 64 ||
        ddr_observed != 64 || par_observed != 64 ||
        ddr_rises != 64 || ddr_falls != 64 || par_frames != 64)
      $fatal(1, "continuous one-per-cycle accounting mismatch");
    $display("W5_A8_BOUND_CONTINUOUS_R1_PASS events=64");

    event_valid_i = 1'b0;
    wait_drain();
    @(negedge sample_clk_i);
    rst_n = 1'b0;
    event_valid_i = 1'b1; event_addr_i = 4'hb;
    repeat (2) @(negedge sample_clk_i);
    rst_n = 1'b1;
    accepted_before = accepted;
    @(posedge ref_clk_i); #1ps;
    if (accepted != accepted_before)
      $fatal(1, "stalled transaction accepted on reset arming edge");
    if (event_addr_i !== 4'hb)
      $fatal(1, "stalled transaction changed before handshake");
    @(posedge ref_clk_i); #1ps;
    if (accepted != accepted_before + 1)
      $fatal(1, "stalled transaction missing first legal handshake");
    @(negedge ref_clk_i); event_valid_i = 1'b0;
    wait_drain();
    if (accepted != 65 || ddr_available != 65 || par_available != 65 ||
        ddr_observed != 65 || par_observed != 65)
      $fatal(1, "held transaction did not deliver exactly once");
    $display("W5_A8_BOUND_STALLED_HELD_VALID_PASS events=1");

    // Reset is deliberately asserted after the DDR rise and before its fall.
    // The in-flight occurrence is contract-invalid and must be aborted without
    // a stale post-reset retirement; a fresh occurrence must then recover.
    @(negedge ref_clk_i);
    accepted_before = accepted;
    event_valid_i = 1'b1;
    event_addr_i = 4'hd;
    @(posedge ref_clk_i); #1ps;
    if (accepted != accepted_before + 1)
      $fatal(1, "reset-test occurrence was not admitted");
    event_valid_i = 1'b0;
    @(posedge ddr_clk); #1ns;
    if (ddr_idle || par_idle)
      $fatal(1, "drain_idle rose during open reset-test frame");
    rst_n = 1'b0;
    #1ps;
    if (ddr_clk !== 1'b0 || ddr_retire_valid || par_retire_valid)
      $fatal(1, "mid-frame reset did not synchronously abort visible state");
    clear_epoch();
    repeat (2) @(negedge sample_clk_i);
    rst_n = 1'b1;
    @(posedge ref_clk_i); #1ps;
    if (!ddr_ready || !par_ready)
      $fatal(1, "mid-frame reset recovery did not re-arm ready");
    repeat (2) begin
      @(posedge ref_clk_i); #1ps;
      if (ddr_retire_valid || par_retire_valid || ddr_available != 0 ||
          par_available != 0 || ddr_observed != 0 || par_observed != 0)
        $fatal(1, "stale post-reset event escaped aborted frame");
    end
    @(negedge ref_clk_i);
    event_addr_i = 4'h6;
    event_valid_i = 1'b1;
    @(negedge ref_clk_i);
    event_valid_i = 1'b0;
    wait_drain();
    if (accepted != 1 || ddr_available != 1 || par_available != 1 ||
        ddr_observed != 1 || par_observed != 1 ||
        ddr_rises != 1 || ddr_falls != 1 || par_frames != 1)
      $fatal(1, "mid-frame reset recovery accounting mismatch");
    $display("W5_A8_BOUND_MIDFRAME_RESET_ABORT_RECOVERY_PASS");

    if (errors != 0) $fatal(1, "independent direct-native errors=%0d", errors);
    $display("W5_A8_BOUND_PRODUCTION_PARALLEL_LOCKSTEP_PASS");
    $finish;
  end
endmodule
