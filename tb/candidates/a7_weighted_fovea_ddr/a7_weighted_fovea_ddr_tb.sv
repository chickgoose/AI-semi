`timescale 1ns/1ps

module a7_weighted_fovea_ddr_tb;
  localparam time HALF = 8ns;
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
      if (protocol_fault_o) begin
        $error("composition protocol fault asserted");
        errors = errors + 1;
      end
    end
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

  task automatic legal_drain_reset;
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
      rst_n = 1'b1;
      wait_endpoint_ready();
    end
  endtask

  task automatic run_full_contention;
    integer target;
    logic [15:0] final_ready;
    begin
      row_accepts[0] = 0; row_accepts[1] = 0;
      row_accepts[2] = 0; row_accepts[3] = 0;
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
      $display("A7_W6_WEIGHT_1_5_5_1_PASS rows=%0d:%0d:%0d:%0d",
               row_accepts[0], row_accepts[1], row_accepts[2], row_accepts[3]);
      $display("A7_W6_CONTINUOUS_FULL_CONTENTION_PASS events=120");
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
        one_shot_mode = 1'b1;
        @(negedge ref_clk_i);
        source_valid = 16'h0040;
        #2ps;
        if (drain_idle_o)
          $fatal(1, "drain high immediately after live same-address request");
        while (source_valid != '0) @(negedge ref_clk_i);
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
    run_same_address_retrigger();

    legal_drain_reset();
    epoch_start_accepted = accepted;
    epoch_start_delivered = retired;
    one_shot_mode = 1'b1;
    @(negedge ref_clk_i);
    source_valid = 16'b1010_0000_0000_0101;
    while (source_valid != '0) @(negedge ref_clk_i);
    one_shot_mode = 1'b0;
    wait_drain();
    if ((accepted - epoch_start_accepted) != 4 ||
        (retired - epoch_start_delivered) != 4)
      $fatal(1, "post-reset exact count mismatch");
    $display("A7_W6_RESET_DRAIN_PASS pre_and_post_epochs_clean");

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
