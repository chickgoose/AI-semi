`timescale 1ns/1ps

module a7_weighted_fovea_ddr_tb;
  localparam time HALF = 8ns;
  localparam logic [15:0] RESET_P = 16'b0100_0010_0001_0010;
  localparam logic [15:0] RESET_Q = 16'b1000_0100_0010_0001;
  logic ref_clk_i, sample_clk_i, rst_n;
  logic [15:0] source_valid;
  logic [15:0] source_ready;
  logic burst_clk_o;
  logic [1:0] burst_data_o;
  logic [3:0] retire_addr_o;
  logic retire_valid_o, drain_idle_o, protocol_fault_o;
  logic consumer_valid_q;
  logic [3:0] consumer_addr_q;
  logic [3:0] expected [0:2047];
  integer accept_cycle [0:2047];
  integer accepted, available, retired, errors, ref_cycle;
  integer row_accepts [0:3];
  integer source;
  integer accepted_source;
  integer epoch_start_accepted, epoch_start_delivered;
  integer full_previous_cycle, full_accept_count;
  bit full_contention_mode;
  bit one_shot_mode;

  a7_weighted_fovea_ddr dut (.*);

  initial begin ref_clk_i = 1'b0; forever #(HALF) ref_clk_i = ~ref_clk_i; end
  initial begin
    sample_clk_i = 1'b0;
    #12ns sample_clk_i = 1'b1;
    forever #(HALF) sample_clk_i = ~sample_clk_i;
  end

  // Real always-ready synchronous consumer.  Nonblocking assignments sample
  // the endpoint output in the pre-NBA region; the scoreboard observes the
  // resulting retirement after the edge, never by peeking at post-NBA output.
  always_ff @(posedge ref_clk_i or negedge rst_n) begin
    if (!rst_n) begin
      consumer_valid_q <= 1'b0;
      consumer_addr_q <= '0;
    end else begin
      consumer_valid_q <= retire_valid_o;
      if (retire_valid_o)
        consumer_addr_q <= retire_addr_o;
    end
  end

  always @(posedge ref_clk_i) begin
    if (!rst_n) begin
      ref_cycle = 0;
    end else begin
      ref_cycle = ref_cycle + 1;
      if (!$onehot0(source_ready)) begin
        $error("source_ready is not onehot0: %h", source_ready);
        errors = errors + 1;
      end
      accepted_source = -1;
      for (source = 0; source < 16; source = source + 1)
        if (source_ready[source])
          accepted_source = source;
      if (accepted_source >= 0) begin
        if (!source_valid[accepted_source]) begin
          $error("ready without live source=%0d", accepted_source);
          errors = errors + 1;
        end
        expected[accepted] = 4'(accepted_source);
        accept_cycle[accepted] = ref_cycle;
        accepted = accepted + 1;
        if (full_contention_mode)
          row_accepts[accepted_source / 4] =
            row_accepts[accepted_source / 4] + 1;
        if (full_contention_mode) begin
          if (full_accept_count != 0 &&
              ref_cycle != full_previous_cycle + 1)
            $fatal(1, "A7_W6_FULL_CONTENTION_BUBBLE_CAUGHT previous=%0d current=%0d count=%0d",
                   full_previous_cycle, ref_cycle, full_accept_count);
          full_previous_cycle = ref_cycle;
          full_accept_count = full_accept_count + 1;
        end
        if (one_shot_mode)
          source_valid[accepted_source] <= 1'b0;
      end
      if ((|source_valid) && drain_idle_o) begin
        $error("drain high with live source_valid=%h", source_valid);
        errors = errors + 1;
      end
      if (dut.endpoint.launch_fire && drain_idle_o) begin
        $error("drain high during same-cycle endpoint launch");
        errors = errors + 1;
      end
      if (!dut.endpoint_drain_idle && drain_idle_o)
        $fatal(1, "A7_W6_ENDPOINT_DRAIN_TERM_REMOVAL_CAUGHT edge=ref_pre");

      #1ps;
      if (retire_valid_o) begin
        if (available >= accepted) begin
          $error("phantom/duplicate retirement addr=%h", retire_addr_o);
          errors = errors + 1;
        end else if (retire_addr_o !== expected[available]) begin
          $error("retire order/address mismatch index=%0d got=%h expected=%h",
                 available, retire_addr_o, expected[available]);
          errors = errors + 1;
        end else if (ref_cycle != accept_cycle[available] + 1) begin
          $error("output availability timing mismatch index=%0d accept=%0d available=%0d",
                 available, accept_cycle[available], ref_cycle);
          errors = errors + 1;
        end
        if (drain_idle_o) begin
          $error("drain high with pending registered retire output");
          errors = errors + 1;
        end
        available = available + 1;
      end
      if (consumer_valid_q) begin
        if (retired >= accepted) begin
          $error("phantom/duplicate consumer retirement addr=%h", consumer_addr_q);
          errors = errors + 1;
        end else if (consumer_addr_q !== expected[retired]) begin
          $error("consumer order/address mismatch index=%0d got=%h expected=%h",
                 retired, consumer_addr_q, expected[retired]);
          errors = errors + 1;
        end else if (ref_cycle != accept_cycle[retired] + 2) begin
          $error("consumer retirement timing mismatch index=%0d accept=%0d retired=%0d",
                 retired, accept_cycle[retired], ref_cycle);
          errors = errors + 1;
        end
        retired = retired + 1;
      end
      if (!dut.endpoint_drain_idle && drain_idle_o)
        $fatal(1, "A7_W6_ENDPOINT_DRAIN_TERM_REMOVAL_CAUGHT edge=ref_post");
      if (protocol_fault_o) begin
        $error("composition protocol fault asserted");
        errors = errors + 1;
      end
    end
  end


  always @(posedge burst_clk_o) begin
    #1ps;
    if (rst_n && !dut.endpoint_drain_idle && drain_idle_o)
      $fatal(1, "A7_W6_ENDPOINT_DRAIN_TERM_REMOVAL_CAUGHT edge=burst_rise");
  end

  always @(negedge burst_clk_o) begin
    #1ps;
    if (rst_n && !dut.endpoint_drain_idle && drain_idle_o)
      $fatal(1, "A7_W6_ENDPOINT_DRAIN_TERM_REMOVAL_CAUGHT edge=burst_fall");
  end

  task automatic wait_endpoint_ready;
    integer timeout;
    begin
      timeout = 0;
      while (!dut.endpoint_ready && timeout < 12) begin
        @(posedge ref_clk_i); timeout = timeout + 1;
      end
      if (timeout == 12)
        $fatal(1, "endpoint safe-release timeout");
    end
  endtask

  task automatic wait_drain;
    integer timeout;
    begin
      timeout = 0;
      while (((available != accepted) || (retired != accepted) ||
              retire_valid_o || consumer_valid_q || !drain_idle_o) &&
             timeout < 256) begin
        @(posedge ref_clk_i); timeout = timeout + 1;
      end
      if (timeout == 256)
        $fatal(1, "drain timeout accepted=%0d available=%0d retired=%0d",
               accepted, available, retired);
    end
  endtask

  task automatic drain_reset_release_live(
    input logic [15:0] live_mask,
    input integer acceptance_base
  );
    integer edge_count;
    begin
      source_valid = '0;
      wait_drain();
      @(negedge sample_clk_i);
      if (!drain_idle_o || ref_clk_i !== 1'b0)
        $fatal(1, "drain reset precondition missing");
      rst_n = 1'b0;
      for (edge_count = 0; edge_count < 3; edge_count = edge_count + 1) begin
        @(posedge ref_clk_i); #1ps;
        if (source_ready != '0 || retire_valid_o || burst_clk_o ||
            dut.fovea_req != '0)
          $fatal(1, "reset quiescence failure");
      end
      @(negedge sample_clk_i);
      source_valid = live_mask;
      one_shot_mode = 1'b1;
      rst_n = 1'b1;
      @(posedge ref_clk_i);  // R0, pre-NBA safe-release boundary
      if (dut.endpoint_ready !== 1'b0 || dut.fovea_req != '0 ||
          source_ready != '0 || dut.endpoint.launch_fire !== 1'b0)
        $fatal(1, "A7_W6_RESET_R0_PRE_BOUNDARY_CAUGHT ready=%b req=%h source_ready=%h launch=%b",
               dut.endpoint_ready, dut.fovea_req, source_ready,
               dut.endpoint.launch_fire);
      #1ps;
      if (dut.endpoint_ready !== 1'b1)
        $fatal(1, "A7_W6_RESET_R0_POST_ARM_CAUGHT ready=%b",
               dut.endpoint_ready);
      if (accepted != acceptance_base)
        $fatal(1, "A7_W6_RESET_R0_EARLY_ACCEPT_CAUGHT accepted=%0d base=%0d",
               accepted, acceptance_base);

      @(posedge ref_clk_i); #1ps;  // R1: fovea result becomes available
      if (accepted != acceptance_base)
        $fatal(1, "A7_W6_RESET_R1_EARLY_ACCEPT_CAUGHT accepted=%0d base=%0d",
               accepted, acceptance_base);

      @(posedge ref_clk_i); #1ps;  // R2: first source acceptance
      if (accepted != acceptance_base + 1)
        $fatal(1, "A7_W6_RESET_R2_FIRST_ACCEPT_CAUGHT accepted=%0d expected=%0d",
               accepted, acceptance_base + 1);
      $display("A7_W6_RESET_R0_R2_TIMELINE_PASS first_accept_cycle=R2");
    end
  endtask

  task automatic run_full_contention;
    integer target;
    logic [15:0] final_ready;
    begin
      row_accepts[0] = 0; row_accepts[1] = 0;
      row_accepts[2] = 0; row_accepts[3] = 0;
      full_previous_cycle = -1;
      full_accept_count = 0;
      full_contention_mode = 1'b1;
      target = accepted + 120;
      @(negedge ref_clk_i);
      source_valid = '1;
      // Stop without withdrawing the transaction already selected by the
      // registered canonical macro: retain only that source for the final
      // handshake, so the same edge makes the next raw result invalid.
      while (accepted < target - 1) @(negedge ref_clk_i);
      final_ready = source_ready;
      if (!$onehot(final_ready))
        $fatal(1, "missing final full-contention selection ready=%h", final_ready);
      source_valid = final_ready;
      @(posedge ref_clk_i); #1ps;
      source_valid = '0;
      full_contention_mode = 1'b0;
      wait_drain();
      if (accepted != target || row_accepts[0] != 10 ||
          row_accepts[1] != 50 || row_accepts[2] != 50 ||
          row_accepts[3] != 10)
        $fatal(1, "weight contract mismatch accepts=%0d rows=%0d:%0d:%0d:%0d",
               accepted, row_accepts[0], row_accepts[1],
               row_accepts[2], row_accepts[3]);
      if (full_accept_count != 120 ||
          accept_cycle[target - 1] - accept_cycle[target - 120] != 119)
        $fatal(1, "full-contention exact cadence mismatch count=%0d first=%0d last=%0d",
               full_accept_count, accept_cycle[target - 120],
               accept_cycle[target - 1]);
      $display("A7_W6_WEIGHT_1_5_5_1_PASS rows=%0d:%0d:%0d:%0d",
               row_accepts[0], row_accepts[1], row_accepts[2], row_accepts[3]);
      $display("A7_W6_CONTINUOUS_FULL_CONTENTION_PASS events=120");
      $display("A7_W6_FULL_CONTENTION_1_PER_CYCLE_PASS intervals=119");
    end
  endtask

  task automatic run_one_each;
    integer target;
    begin
      epoch_start_accepted = accepted;
      epoch_start_delivered = retired;
      target = accepted + 16;
      one_shot_mode = 1'b1;
      @(negedge ref_clk_i); source_valid = '1;
      while (source_valid != '0) @(negedge ref_clk_i);
      one_shot_mode = 1'b0;
      wait_drain();
      if (accepted != target || available != target || retired != target ||
          accepted - epoch_start_accepted != 16 ||
          retired - epoch_start_delivered != 16)
        $fatal(1, "one-each exact count mismatch");
      $display("A7_W6_ONE_EACH_ORDER_PASS events=16");
    end
  endtask

  task automatic run_same_address_retrigger;
    integer start_accepted;
    integer start_retired;
    integer attempt;
    begin
      start_accepted = accepted;
      start_retired = retired;
      for (attempt = 0; attempt < 2; attempt = attempt + 1) begin
        integer grant_timeout;
        one_shot_mode = 1'b1;
        @(negedge ref_clk_i);
        source_valid = 16'h0040;
        #2ps;
        if (drain_idle_o)
          $fatal(1, "drain high immediately after live same-address request");
        grant_timeout = 0;
        while (source_valid != '0 && grant_timeout < 32) begin
          @(negedge ref_clk_i);
          grant_timeout = grant_timeout + 1;
        end
        if (grant_timeout == 32)
          $fatal(1, "A7_W6_SECOND_GRANT_SUPPRESSION_CAUGHT attempt=%0d",
                 attempt);
        one_shot_mode = 1'b0;
        wait_drain();
        repeat (2) begin
          @(posedge ref_clk_i); #1ps;
          if (!drain_idle_o)
            $fatal(1, "quiet interval did not remain drained");
        end
      end
      if (accepted - start_accepted != 2 || retired - start_retired != 2 ||
          expected[start_accepted] != 4'h6 ||
          expected[start_accepted + 1] != 4'h6)
        $fatal(1, "same-address legal retrigger mismatch");
      $display("A7_W6_SAME_ADDRESS_RETRIGGER_PASS addr=6 events=2");
    end
  endtask

  task automatic run_epoch_mask(
    input logic [15:0] mask,
    input integer expected_count
  );
    integer timeout;
    begin
      one_shot_mode = 1'b1;
      @(negedge ref_clk_i);
      source_valid = mask;
      timeout = 0;
      while (source_valid != '0 && timeout < 64) begin
        @(negedge ref_clk_i);
        timeout = timeout + 1;
      end
      if (timeout == 64)
        $fatal(1, "epoch mask drain timeout mask=%h", mask);
      one_shot_mode = 1'b0;
      wait_drain();
      if (expected_count != 4)
        $fatal(1, "internal epoch expected-count contract mismatch");
    end
  endtask

  initial begin
    rst_n = 1'b0;
    source_valid = '0;
    accepted = 0;
    available = 0;
    retired = 0;
    errors = 0;
    ref_cycle = 0;
    full_contention_mode = 1'b0;
    one_shot_mode = 1'b0;
    repeat (3) @(negedge sample_clk_i);
    rst_n = 1'b1;
    wait_endpoint_ready();

    run_full_contention();
    run_one_each();

    // Reset epoch proof: every P address completes before reset.  Q is
    // disjoint and already live at release so the R0/R1/R2 timeline is exact.
    epoch_start_accepted = accepted;
    epoch_start_delivered = retired;
    run_epoch_mask(RESET_P, 4); // P={1,4,9,14}
    if (accepted - epoch_start_accepted != 4 ||
        retired - epoch_start_delivered != 4)
      $fatal(1, "pre-reset P epoch exact-count mismatch");
    begin : check_p_epoch
      integer p_index;
      logic [15:0] p_seen;
      p_seen = '0;
      for (p_index = epoch_start_accepted; p_index < accepted;
           p_index = p_index + 1) begin
        if (!RESET_P[expected[p_index]])
          $fatal(1, "pre-reset P foreign addr=%0d index=%0d",
                 expected[p_index], p_index);
        if (p_seen[expected[p_index]])
          $fatal(1, "pre-reset P duplicate addr=%0d", expected[p_index]);
        p_seen[expected[p_index]] = 1'b1;
      end
      if (p_seen != RESET_P || (RESET_P & RESET_Q) != '0)
        $fatal(1, "reset P/Q disjoint epoch contract mismatch Pseen=%h", p_seen);
    end

    epoch_start_accepted = accepted;
    epoch_start_delivered = retired;
    drain_reset_release_live(RESET_Q,
                             epoch_start_accepted); // Q={0,5,10,15}
    begin : finish_q_epoch
      integer q_timeout;
      q_timeout = 0;
      while (source_valid != '0 && q_timeout < 64) begin
        @(negedge ref_clk_i);
        q_timeout = q_timeout + 1;
      end
      if (q_timeout == 64)
        $fatal(1, "post-reset Q epoch timeout");
    end
    one_shot_mode = 1'b0;
    wait_drain();
    if ((accepted - epoch_start_accepted) != 4 ||
        (retired - epoch_start_delivered) != 4)
      $fatal(1, "post-reset exact count mismatch");
    begin : check_q_epoch
      integer q_index;
      logic [15:0] q_seen;
      q_seen = '0;
      for (q_index = epoch_start_accepted; q_index < accepted;
           q_index = q_index + 1) begin
        if (!RESET_Q[expected[q_index]])
          $fatal(1, "A7_W6_RESET_STALE_EPOCH_CAUGHT addr=%0d index=%0d",
                 expected[q_index], q_index);
        if (q_seen[expected[q_index]])
          $fatal(1, "post-reset Q duplicate addr=%0d", expected[q_index]);
        q_seen[expected[q_index]] = 1'b1;
      end
      if (q_seen != RESET_Q)
        $fatal(1, "post-reset Q coverage mismatch seen=%h", q_seen);
    end
    $display("A7_W6_RESET_DISJOINT_EPOCH_PASS P=1,4,9,14 Q=0,5,10,15");
    $display("A7_W6_RESET_DRAIN_PASS pre_and_post_epochs_clean");

    // Run after reset so the second-grant mutant starts with no address-6
    // history; the first occurrence must succeed and only the retrigger fails.
    run_same_address_retrigger();

    if (accepted != available || accepted != retired || errors != 0 ||
        protocol_fault_o)
      $fatal(1, "W6 correctness failure accepted=%0d available=%0d retired=%0d errors=%0d fault=%b",
             accepted, available, retired, errors, protocol_fault_o);
    $display("A7_W6_OUTPUT_AVAILABLE_CYCLE1_PASS events=%0d", available);
    $display("A7_W6_CONSUMER_RETIRE_CYCLE2_PASS events=%0d", retired);
    $display("A7_W6_DRAIN_GUARDS_PASS live_launch_pending=1");
    $display("A7_W6_NO_DUP_ORDER_ADDRESS_PASS accepted=%0d available=%0d retired=%0d",
             accepted, available, retired);
    $display("A7_W6_WEIGHTED_FOVEA_DDR_DIRECTED_RTL_REGRESSION_PASS");
    $finish;
  end
endmodule
