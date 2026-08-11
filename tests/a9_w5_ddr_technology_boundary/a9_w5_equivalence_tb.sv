`timescale 1ns/1ps

module a9_w5_equivalence_tb;
  localparam time HALF = 8ns;
  logic ref_clk_i, sample_clk_i, rst_n, event_valid_i;
  logic [3:0] event_addr_i;

  logic a7_ready, a7_clk, a7_valid, a7_idle;
  logic [1:0] a7_data;
  logic [3:0] a7_addr;
  logic par_ready, par_clk, par_valid, par_idle;
  logic [3:0] par_data, par_addr;
  logic dut_ready, dut_clk, dut_valid, dut_idle;
  logic [1:0] dut_data;
  logic [3:0] dut_addr;
  logic a7_consumer_valid_q, par_consumer_valid_q, dut_consumer_valid_q;
  logic [3:0] a7_consumer_addr_q, par_consumer_addr_q, dut_consumer_addr_q;

  logic [3:0] expected [0:127];
  integer accepted_cycle [0:127];
  integer accepted, a7_available, par_available, dut_available;
  integer a7_consumed, par_consumed, dut_consumed, errors, ref_cycle;

  a7_r1_candidate_endpoint exact_a7 (
    .ref_clk_i, .sample_clk_i, .rst_n, .event_valid_i, .event_addr_i,
    .event_ready_o(a7_ready), .burst_clk_o(a7_clk),
    .burst_data_o(a7_data), .retire_addr_o(a7_addr),
    .retire_valid_o(a7_valid), .drain_idle_o(a7_idle));

  a7_r1_parallel_reference_top exact_parallel (
    .ref_clk_i, .sample_clk_i, .rst_n, .event_valid_i, .event_addr_i,
    .event_ready_o(par_ready), .link_strobe_o(par_clk),
    .link_data_o(par_data), .retire_addr_o(par_addr),
    .retire_valid_o(par_valid), .drain_idle_o(par_idle));

  a9_w5_ddr_link dut (
    .ref_clk_i, .sample_clk_i, .rst_n, .event_valid_i, .event_addr_i,
    .event_ready_o(dut_ready), .burst_clk_o(dut_clk),
    .burst_data_o(dut_data), .retire_addr_o(dut_addr),
    .retire_valid_o(dut_valid), .drain_idle_o(dut_idle));

  initial begin ref_clk_i = 1'b0; forever #(HALF) ref_clk_i = ~ref_clk_i; end
  initial begin sample_clk_i = 1'b0; #12ns; sample_clk_i = 1'b1;
    forever #(HALF) sample_clk_i = ~sample_clk_i; end

  always_ff @(posedge ref_clk_i or negedge rst_n) begin
    if (!rst_n) begin
      a7_consumer_valid_q <= 1'b0;
      par_consumer_valid_q <= 1'b0;
      dut_consumer_valid_q <= 1'b0;
      a7_consumer_addr_q <= '0;
      par_consumer_addr_q <= '0;
      dut_consumer_addr_q <= '0;
    end else begin
      a7_consumer_valid_q <= a7_valid;
      par_consumer_valid_q <= par_valid;
      dut_consumer_valid_q <= dut_valid;
      if (a7_valid) a7_consumer_addr_q <= a7_addr;
      if (par_valid) par_consumer_addr_q <= par_addr;
      if (dut_valid) dut_consumer_addr_q <= dut_addr;
    end
  end

  always @(posedge ref_clk_i) begin
    ref_cycle = ref_cycle + 1;
    if (rst_n && event_valid_i && a7_ready) begin
      if (!par_ready || !dut_ready) $fatal(1, "ready mismatch on handshake");
      expected[accepted] = event_addr_i;
      accepted_cycle[accepted] = ref_cycle;
      accepted = accepted + 1;
    end
    #1ps;
    if (rst_n) begin
      if ((a7_ready !== par_ready) || (a7_ready !== dut_ready))
        $fatal(1, "ready boundary mismatch a7=%b par=%b dut=%b",
               a7_ready, par_ready, dut_ready);
      if ((a7_valid !== par_valid) || (a7_valid !== dut_valid))
        $fatal(1, "retire-valid mismatch a7=%b par=%b dut=%b",
               a7_valid, par_valid, dut_valid);
      if (a7_valid) begin
        if (a7_addr !== expected[a7_available] ||
            (ref_cycle - accepted_cycle[a7_available]) != 1)
          $fatal(1, "A7 availability mismatch index=%0d", a7_available);
        a7_available = a7_available + 1;
      end
      if (par_valid) begin
        if (par_addr !== expected[par_available] ||
            (ref_cycle - accepted_cycle[par_available]) != 1)
          $fatal(1, "parallel availability mismatch index=%0d", par_available);
        par_available = par_available + 1;
      end
      if (dut_valid) begin
        if (dut_addr !== expected[dut_available] ||
            (ref_cycle - accepted_cycle[dut_available]) != 1)
          $fatal(1, "A9 availability mismatch index=%0d got=%h expected=%h",
                 dut_available, dut_addr, expected[dut_available]);
        dut_available = dut_available + 1;
      end
      if ((a7_consumer_valid_q !== par_consumer_valid_q) ||
          (a7_consumer_valid_q !== dut_consumer_valid_q))
        $fatal(1, "synchronous consumer valid mismatch");
      if (a7_consumer_valid_q) begin
        if ((a7_consumer_addr_q !== expected[a7_consumed]) ||
            (ref_cycle - accepted_cycle[a7_consumed]) != 2)
          $fatal(1, "A7 consumer latency mismatch index=%0d", a7_consumed);
        a7_consumed = a7_consumed + 1;
      end
      if (par_consumer_valid_q) begin
        if ((par_consumer_addr_q !== expected[par_consumed]) ||
            (ref_cycle - accepted_cycle[par_consumed]) != 2)
          $fatal(1, "parallel consumer latency mismatch index=%0d", par_consumed);
        par_consumed = par_consumed + 1;
      end
      if (dut_consumer_valid_q) begin
        if ((dut_consumer_addr_q !== expected[dut_consumed]) ||
            (ref_cycle - accepted_cycle[dut_consumed]) != 2)
          $fatal(1, "A9 consumer latency mismatch index=%0d", dut_consumed);
        dut_consumed = dut_consumed + 1;
      end
      if ((a7_idle !== par_idle) || (a7_idle !== dut_idle))
        $fatal(1, "drain-idle mismatch a7=%b par=%b dut=%b",
               a7_idle, par_idle, dut_idle);
      if ((a7_valid || dut_valid) && (a7_idle || dut_idle))
        $fatal(1, "pending registered output reported idle");
    end
  end

  always @(a7_clk or dut_clk or a7_data or dut_data) begin
    #1ps;
    if (rst_n && ((a7_clk !== dut_clk) || (a7_data !== dut_data))) begin
      $display("raw DDR mismatch t=%0t a7=%b/%h dut=%b/%h",
               $realtime, a7_clk, a7_data, dut_clk, dut_data);
      errors = errors + 1;
    end
  end

  task automatic drive_cycle(input logic valid, input logic [3:0] addr);
    @(negedge ref_clk_i);
    event_valid_i = valid;
    event_addr_i = addr;
  endtask

  task automatic wait_drain;
    integer timeout;
    begin
      timeout = 0;
      while (((a7_consumed != accepted) || (par_consumed != accepted) ||
              (dut_consumed != accepted) || a7_consumer_valid_q ||
              par_consumer_valid_q || dut_consumer_valid_q ||
              !a7_idle || !par_idle || !dut_idle) &&
             timeout < 80) begin
        @(posedge ref_clk_i);
        timeout = timeout + 1;
      end
      if (timeout == 80) $fatal(1, "drain timeout accepted=%0d a7=%0d par=%0d dut=%0d",
                                accepted, a7_consumed, par_consumed, dut_consumed);
    end
  endtask

  integer index;
  integer before_held_accept;
  initial begin
    rst_n = 1'b0; event_valid_i = 1'b0; event_addr_i = '0;
    accepted = 0; a7_available = 0; par_available = 0; dut_available = 0;
    a7_consumed = 0; par_consumed = 0; dut_consumed = 0;
    errors = 0; ref_cycle = 0;
    repeat (3) @(negedge sample_clk_i);
    rst_n = 1'b1;

    // First ref edge arms ready but cannot accept the held transaction.
    event_valid_i = 1'b1; event_addr_i = 4'h9;
    @(posedge ref_clk_i); #1ps;
    if (!a7_ready || !par_ready || !dut_ready || accepted != 0)
      $fatal(1, "reset-release arming mismatch");
    if (a7_idle || par_idle || dut_idle)
      $fatal(1, "same-cycle held launch request reported idle");
    @(posedge ref_clk_i);
    @(negedge ref_clk_i); event_valid_i = 1'b0;

    // Continuous valid with changing address is one handshake per ref edge.
    @(negedge ref_clk_i); event_valid_i = 1'b1;
    for (index = 0; index < 16; index = index + 1) begin
      event_addr_i = ((index * 5) + 3) & 15;
      if (index != 15) @(negedge ref_clk_i);
    end
    @(negedge ref_clk_i); event_valid_i = 1'b0; event_addr_i = 4'hf;
    wait_drain();
    if ((accepted != 17) || (a7_available != 17) ||
        (par_available != 17) || (dut_available != 17) ||
        (a7_consumed != 17) || (par_consumed != 17) ||
        (dut_consumed != 17) || errors)
      $fatal(1, "nominal/continuous mismatch");

    // Legal drained reset, then another held-valid arming transaction.
    @(negedge sample_clk_i);
    if (!a7_idle || !par_idle || !dut_idle || ref_clk_i !== 1'b0)
      $fatal(1, "legal reset precondition missing");
    rst_n = 1'b0;
    event_valid_i = 1'b1; event_addr_i = 4'hb;
    before_held_accept = accepted;
    repeat (2) @(negedge sample_clk_i);
    rst_n = 1'b1;
    @(posedge ref_clk_i); #1ps;
    if (accepted != before_held_accept || !dut_ready)
      $fatal(1, "held-valid arming edge charged incorrectly");
    @(posedge ref_clk_i);
    @(negedge ref_clk_i); event_valid_i = 1'b0;
    wait_drain();
    if ((accepted != 18) || (a7_available != 18) ||
        (par_available != 18) || (dut_available != 18) ||
        (a7_consumed != 18) || (par_consumed != 18) ||
        (dut_consumed != 18) || errors)
      $fatal(1, "post-reset held-valid mismatch");

    $display("A9_W5_42377CA_EQUIVALENCE_PASS accepted=18 available=18 consumed=18");
    $finish;
  end

  initial begin
    #5000ns;
    $fatal(1, "watchdog accepted=%0d a7=%0d par=%0d dut=%0d",
           accepted, a7_consumed, par_consumed, dut_consumed);
  end
endmodule
