`timescale 1ns/1ps

module redred_single_edge_smoke_tb;
  logic clk = 1'b0;
  logic rst = 1'b1;
  always #5 clk = ~clk;

  logic raw_enable;
  logic [1:0] raw_count;
  logic [3:0] raw_addr0, raw_addr1;
  logic raw_ready, raw_commit;
  logic [1:0] raw_microsteps;
  logic raw_error;
  logic raw_link_valid;
  logic [3:0] raw_link_addr0, raw_link_addr1;
  logic [1:0] raw_retire_valid;
  logic [3:0] raw_retire_addr0, raw_retire_addr1;
  logic raw_drain;

  logic bad_link_valid;
  logic [3:0] bad_link_addr0, bad_link_addr1;
  logic [1:0] bad_retire_valid;
  logic [3:0] bad_retire_addr0, bad_retire_addr1;
  logic bad_error;
  logic sticky_probe_event;
  logic sticky_probe_error;

  logic a2_enable;
  logic [15:0] a2_pending;
  logic [15:0] a2_accept;
  logic [1:0] a2_accept_count;
  logic [3:0] a2_accept_addr0, a2_accept_addr1;
  logic a2_link_valid;
  logic [3:0] a2_link_addr0, a2_link_addr1;
  logic [1:0] a2_retire_valid;
  logic [3:0] a2_retire_addr0, a2_retire_addr1;
  logic a2_error, a2_drain;

  logic a3_enable;
  logic [15:0] a3_pending;
  logic [15:0] a3_accept;
  logic [1:0] a3_accept_count;
  logic [3:0] a3_accept_addr0, a3_accept_addr1;
  logic a3_link_valid;
  logic [3:0] a3_link_addr0, a3_link_addr1;
  logic [1:0] a3_retire_valid;
  logic [3:0] a3_retire_addr0, a3_retire_addr1;
  logic a3_error, a3_drain;

  logic [3:0] a2_queue [0:63];
  logic [3:0] a3_queue [0:63];
  logic [3:0] raw_queue [0:255];
  integer a2_head, a2_tail, a3_head, a3_tail;
  integer a2_accepted, a2_retired, a3_accepted, a3_retired;
  integer raw_head, raw_tail, raw_accepted, raw_retired;
  integer raw_score_enable;
  integer saw_a2_consume_refill;
  integer addr0_index, addr1_index, record_index;
  integer timeout;
  logic [1:0] a3_held_count;
  logic [3:0] a3_held_addr0, a3_held_addr1;

  w2_single_edge_exact_pair_endpoint raw_endpoint (
    .clk_i(clk), .rst_i(rst), .link_enable_i(raw_enable),
    .input_count_i(raw_count), .input_addr0_i(raw_addr0),
    .input_addr1_i(raw_addr1), .input_ready_o(raw_ready),
    .input_commit_o(raw_commit), .policy_microsteps_o(raw_microsteps),
    .protocol_error_o(raw_error), .link_valid_o(raw_link_valid),
    .link_addr0_o(raw_link_addr0), .link_addr1_o(raw_link_addr1),
    .retire_valid_o(raw_retire_valid),
    .retire_addr0_o(raw_retire_addr0),
    .retire_addr1_o(raw_retire_addr1), .drain_idle_o(raw_drain)
  );

  w2_single_edge_pair_rx malformed_rx (
    .clk_i(clk), .rst_i(rst), .link_valid_i(bad_link_valid),
    .link_addr0_i(bad_link_addr0), .link_addr1_i(bad_link_addr1),
    .retire_valid_o(bad_retire_valid),
    .retire_addr0_o(bad_retire_addr0),
    .retire_addr1_o(bad_retire_addr1), .protocol_error_o(bad_error)
  );

  w2_single_edge_error_latch sticky_probe (
    .clk_i(clk), .rst_i(rst), .error_event_i(sticky_probe_event),
    .protocol_error_o(sticky_probe_error)
  );

  a2_batched_iwrr_single_edge_top a2 (
    .clk_i(clk), .rst_i(rst), .link_enable_i(a2_enable),
    .source_pending_i(a2_pending), .source_accept_o(a2_accept),
    .accept_count_o(a2_accept_count), .accept_addr0_o(a2_accept_addr0),
    .accept_addr1_o(a2_accept_addr1), .link_valid_o(a2_link_valid),
    .link_addr0_o(a2_link_addr0), .link_addr1_o(a2_link_addr1),
    .retire_valid_o(a2_retire_valid), .retire_addr0_o(a2_retire_addr0),
    .retire_addr1_o(a2_retire_addr1), .protocol_error_o(a2_error),
    .drain_idle_o(a2_drain)
  );

  a3_exact_scalar_prefix_k2_single_edge_top a3 (
    .clk_i(clk), .rst_i(rst), .link_enable_i(a3_enable),
    .source_pending_i(a3_pending), .source_accept_o(a3_accept),
    .accept_count_o(a3_accept_count), .accept_addr0_o(a3_accept_addr0),
    .accept_addr1_o(a3_accept_addr1), .link_valid_o(a3_link_valid),
    .link_addr0_o(a3_link_addr0), .link_addr1_o(a3_link_addr1),
    .retire_valid_o(a3_retire_valid), .retire_addr0_o(a3_retire_addr0),
    .retire_addr1_o(a3_retire_addr1), .protocol_error_o(a3_error),
    .drain_idle_o(a3_drain)
  );

  function automatic integer popcount16(input logic [15:0] bits);
    integer index;
    begin
      popcount16 = 0;
      for (index = 0; index < 16; index = index + 1)
        popcount16 = popcount16 + bits[index];
    end
  endfunction

  always @(posedge clk) begin
    if (rst) begin
      a2_head = 0;
      a2_tail = 0;
      a3_head = 0;
      a3_tail = 0;
      a2_accepted = 0;
      a2_retired = 0;
      a3_accepted = 0;
      a3_retired = 0;
      raw_head = 0;
      raw_tail = 0;
      raw_accepted = 0;
      raw_retired = 0;
      saw_a2_consume_refill = 0;
    end else begin
      if (!raw_enable && raw_commit)
        $fatal(1, "raw endpoint committed while disabled");
      if (!a2_enable && (a2_accept_count != 2'd0 || a2_accept != 16'd0))
        $fatal(1, "A2 accepted while disabled");
      if (!a3_enable && (a3_accept_count != 2'd0 || a3_accept != 16'd0))
        $fatal(1, "A3 accepted while disabled");

      if (raw_score_enable != 0) begin
        if (raw_retire_valid[0]) begin
          if (raw_head >= raw_tail ||
              raw_retire_addr0 !== raw_queue[raw_head])
            $fatal(1, "raw retirement head identity/order mismatch");
          raw_head = raw_head + 1;
          raw_retired = raw_retired + 1;
        end
        if (raw_retire_valid[1]) begin
          if (!raw_retire_valid[0] || raw_head >= raw_tail ||
              raw_retire_addr1 !== raw_queue[raw_head])
            $fatal(1, "raw retirement lane1 identity/order mismatch");
          raw_head = raw_head + 1;
          raw_retired = raw_retired + 1;
        end
        if (raw_commit) begin
          raw_queue[raw_tail] = raw_addr0;
          raw_tail = raw_tail + 1;
          raw_accepted = raw_accepted + 1;
          if (raw_count == 2'd2) begin
            raw_queue[raw_tail] = raw_addr1;
            raw_tail = raw_tail + 1;
            raw_accepted = raw_accepted + 1;
          end
        end
      end

      if (a2.endpoint_commit && a2.scheduler_commit)
        saw_a2_consume_refill = 1;

      if (a2_retire_valid[0]) begin
        if (a2_head >= a2_tail || a2_retire_addr0 !== a2_queue[a2_head])
          $fatal(1, "A2 retirement head identity/order mismatch");
        a2_head = a2_head + 1;
        a2_retired = a2_retired + 1;
      end
      if (a2_retire_valid[1]) begin
        if (!a2_retire_valid[0] || a2_head >= a2_tail ||
            a2_retire_addr1 !== a2_queue[a2_head])
          $fatal(1, "A2 retirement lane1 identity/order mismatch");
        a2_head = a2_head + 1;
        a2_retired = a2_retired + 1;
      end
      if (a2_accept_count != 2'd0) begin
        if (popcount16(a2_accept) != a2_accept_count ||
            (a2_accept & ~a2_pending) != 16'd0)
          $fatal(1, "A2 accept bitmap/count/live-source mismatch");
        a2_queue[a2_tail] = a2_accept_addr0;
        a2_tail = a2_tail + 1;
        a2_accepted = a2_accepted + 1;
        if (a2_accept_count == 2'd2) begin
          if (a2_accept_addr0 == a2_accept_addr1)
            $fatal(1, "A2 accepted duplicate pair");
          a2_queue[a2_tail] = a2_accept_addr1;
          a2_tail = a2_tail + 1;
          a2_accepted = a2_accepted + 1;
        end
        a2_pending <= a2_pending & ~a2_accept;
      end

      if (a3_retire_valid[0]) begin
        if (a3_head >= a3_tail || a3_retire_addr0 !== a3_queue[a3_head])
          $fatal(1, "A3 retirement head identity/order mismatch");
        a3_head = a3_head + 1;
        a3_retired = a3_retired + 1;
      end
      if (a3_retire_valid[1]) begin
        if (!a3_retire_valid[0] || a3_head >= a3_tail ||
            a3_retire_addr1 !== a3_queue[a3_head])
          $fatal(1, "A3 retirement lane1 identity/order mismatch");
        a3_head = a3_head + 1;
        a3_retired = a3_retired + 1;
      end
      if (a3_accept_count != 2'd0) begin
        if (popcount16(a3_accept) != a3_accept_count ||
            (a3_accept & ~a3_pending) != 16'd0)
          $fatal(1, "A3 accept bitmap/count/live-source mismatch");
        a3_queue[a3_tail] = a3_accept_addr0;
        a3_tail = a3_tail + 1;
        a3_accepted = a3_accepted + 1;
        if (a3_accept_count == 2'd2) begin
          if (a3_accept_addr0 == a3_accept_addr1)
            $fatal(1, "A3 accepted duplicate pair");
          a3_queue[a3_tail] = a3_accept_addr1;
          a3_tail = a3_tail + 1;
          a3_accepted = a3_accepted + 1;
        end
        a3_pending <= a3_pending & ~a3_accept;
      end

      if (a2_error || a3_error)
        $fatal(1, "integrated endpoint raised protocol_error");
    end
  end

  initial begin
    raw_enable = 1'b0;
    raw_count = 2'd0;
    raw_addr0 = 4'd0;
    raw_addr1 = 4'd0;
    bad_link_valid = 1'b0;
    bad_link_addr0 = 4'd0;
    bad_link_addr1 = 4'd0;
    sticky_probe_event = 1'b0;
    a2_enable = 1'b0;
    a3_enable = 1'b0;
    a2_pending = 16'd0;
    a3_pending = 16'd0;
    raw_score_enable = 0;
    a3_held_count = 2'd0;
    a3_held_addr0 = 4'd0;
    a3_held_addr1 = 4'd0;

    repeat (2) @(posedge clk);
    #1 rst = 1'b0;

    // Every malformed scheduler-side class fails closed, becomes sticky, and
    // prevents clean drain until synchronous reset.  Count-one's unused field
    // is canonical zero rather than a don't-care.
    raw_enable = 1'b1;
    raw_count = 2'd3;
    #1;
    if (raw_ready || raw_commit || !raw_error || raw_drain)
      $fatal(1, "illegal count did not fail closed");
    @(posedge clk); #1;
    raw_count = 2'd0;
    if (!raw_error || raw_drain)
      $fatal(1, "TX protocol_error was not sticky/clean-drain blocking");

    rst = 1'b1;
    @(posedge clk); #1;
    if (raw_error || raw_link_valid || raw_retire_valid != 2'b00 || !raw_drain)
      $fatal(1, "synchronous reset did not flush the raw endpoint");
    rst = 1'b0;

    raw_count = 2'd0;
    raw_addr0 = 4'd3;
    raw_addr1 = 4'd0;
    #1;
    if (raw_ready || raw_commit || !raw_error || raw_drain)
      $fatal(1, "malformed idle input was not rejected");
    @(posedge clk); #1;
    raw_addr0 = 4'd0;
    if (!raw_error || raw_drain)
      $fatal(1, "malformed idle error was not sticky");
    rst = 1'b1;
    @(posedge clk); #1;
    rst = 1'b0;

    raw_count = 2'd2;
    raw_addr0 = 4'd6;
    raw_addr1 = 4'd6;
    #1;
    if (raw_ready || raw_commit || !raw_error || raw_drain)
      $fatal(1, "equal-address pair was not rejected");
    @(posedge clk); #1;
    raw_count = 2'd0;
    raw_addr0 = 4'd0;
    raw_addr1 = 4'd0;
    if (!raw_error || raw_drain)
      $fatal(1, "equal-pair error was not sticky");
    rst = 1'b1;
    @(posedge clk); #1;
    rst = 1'b0;

    raw_count = 2'd1;
    raw_addr0 = 4'd5;
    raw_addr1 = 4'd9;
    #1;
    if (raw_ready || raw_commit || !raw_error || raw_drain)
      $fatal(1, "nonzero singleton unused field was not rejected");
    @(posedge clk); #1;
    raw_count = 2'd0;
    raw_addr0 = 4'd0;
    raw_addr1 = 4'd0;
    if (!raw_error || raw_drain)
      $fatal(1, "singleton-field error was not sticky");
    rst = 1'b1;
    @(posedge clk); #1;
    rst = 1'b0;

    // Exhaust every legal wire state: one idle plus all 256 valid address
    // combinations.  Equality is singleton; inequality is ordered pair.
    bad_link_valid = 1'b0;
    bad_link_addr0 = 4'd0;
    bad_link_addr1 = 4'd0;
    @(posedge clk); #1;
    if (bad_retire_valid != 2'b00 || bad_error)
      $fatal(1, "legal idle wire state failed");
    for (addr0_index = 0; addr0_index < 16; addr0_index = addr0_index + 1) begin
      for (addr1_index = 0; addr1_index < 16;
           addr1_index = addr1_index + 1) begin
        bad_link_valid = 1'b1;
        bad_link_addr0 = addr0_index[3:0];
        bad_link_addr1 = addr1_index[3:0];
        @(posedge clk); #1;
        if (bad_error || bad_retire_addr0 != addr0_index[3:0])
          $fatal(1, "legal wire state raised error/corrupted lane0");
        if (addr0_index == addr1_index) begin
          if (bad_retire_valid != 2'b01 || bad_retire_addr1 != 4'd0)
            $fatal(1, "equality singleton decode mismatch");
        end else begin
          if (bad_retire_valid != 2'b11 ||
              bad_retire_addr1 != addr1_index[3:0])
            $fatal(1, "distinct ordered-pair decode mismatch");
        end
      end
    end
    bad_link_valid = 1'b0;
    bad_link_addr0 = 4'd0;
    bad_link_addr1 = 4'd0;
    @(posedge clk); #1;
    if (bad_retire_valid != 2'b00 || bad_error)
      $fatal(1, "legal-state exhaustive sweep did not end cleanly");

    // The 255 invalid wire states are one class: idle with nonzero payload.
    bad_link_addr0 = 4'd7;
    @(posedge clk); #1;
    bad_link_addr0 = 4'd0;
    if (!bad_error || bad_retire_valid != 2'b00)
      $fatal(1, "malformed idle payload was not reported without retirement");

    rst = 1'b1;
    @(posedge clk); #1;
    rst = 1'b0;
    if (bad_error)
      $fatal(1, "RX protocol_error did not clear on reset");

    // Long back-to-back alternating singleton/pair traffic must preserve every
    // identity and order while sustaining one record per cycle.
    raw_score_enable = 1;
    raw_enable = 1'b1;
    for (record_index = 0; record_index < 64;
         record_index = record_index + 1) begin
      if ((record_index & 1) == 0) begin
        raw_count = 2'd1;
        raw_addr0 = (record_index * 3) & 15;
        raw_addr1 = 4'd0;
      end else begin
        raw_count = 2'd2;
        raw_addr0 = (record_index * 5) & 15;
        raw_addr1 = (((record_index * 5) & 15) + 7) & 15;
      end
      #1;
      if (!raw_commit || raw_microsteps != raw_count)
        $fatal(1, "legal back-to-back record was not admitted");
      @(posedge clk); #1;
      if (!raw_link_valid || raw_link_addr0 != raw_addr0 ||
          ((raw_count == 2'd1) && (raw_link_addr1 != raw_addr0)) ||
          ((raw_count == 2'd2) && (raw_link_addr1 != raw_addr1)))
        $fatal(1, "back-to-back link encoding mismatch");
    end
    raw_count = 2'd0;
    raw_addr0 = 4'd0;
    raw_addr1 = 4'd0;
    timeout = 0;
    while (!raw_drain && (timeout < 10)) begin
      @(posedge clk); #1;
      timeout = timeout + 1;
    end
    if (!raw_drain || raw_error || raw_accepted != 96 || raw_retired != 96 ||
        raw_head != raw_tail)
      $fatal(1, "long back-to-back conservation/order failure");
    raw_score_enable = 0;

    // Reset establishes a fresh owner-scoreboard phase.
    rst = 1'b1;
    @(posedge clk); #1;
    rst = 1'b0;

    // Both real owners form offers while disabled but accept nothing.
    a2_pending = 16'hffff;
    a3_pending = 16'hca61;
    repeat (4) begin @(posedge clk); #1; end
    if (a2_accept_count != 0 || a3_accept_count != 0 ||
        a2_retire_valid != 0 || a3_retire_valid != 0 ||
        a2_link_valid || a3_link_valid || a3.scheduler_count == 0)
      $fatal(1, "link disable admitted or retired work");
    a3_held_count = a3.scheduler_count;
    a3_held_addr0 = a3.scheduler_addr0;
    a3_held_addr1 = a3.scheduler_addr1;

    // Accept one A2 record into its charged buffer, disable before endpoint
    // consumption, and prove the exact record waits for re-enable.
    a2_enable = 1'b1;
    while (a2_accepted == 0) begin @(posedge clk); #1; end
    if (!a2.buffer_valid_q)
      $fatal(1, "A2 first acceptance did not charge its buffer");
    record_index = a2_accepted;
    a2_enable = 1'b0;
    repeat (3) begin
      @(posedge clk); #1;
      if (a2_accepted != record_index || !a2.buffer_valid_q ||
          a2_link_valid || a2_drain)
        $fatal(1, "disabled A2 lost/consumed/refilled its charged buffer");
    end

    // Toggle admission while A2 repeatedly consumes/refills. Already launched
    // cells may retire during disabled cycles; no new source acceptance may.
    for (record_index = 0; record_index < 24;
         record_index = record_index + 1) begin
      a2_enable = ((record_index % 4) != 1);
      @(posedge clk); #1;
    end
    a2_enable = 1'b1;

    timeout = 0;
    while (!a2_drain && (timeout < 80)) begin
      @(posedge clk); #1;
      timeout = timeout + 1;
    end
    if (timeout >= 80)
      $fatal(1, "A2 endpoint failed to drain");
    if (a2_accepted != 16 || a2_retired != 16 || a2_head != a2_tail ||
        !saw_a2_consume_refill)
      $fatal(1, "A2 conservation mismatch accepted=%0d retired=%0d",
             a2_accepted, a2_retired);

    // A3 remained disabled throughout A2's campaign: its held registered
    // offer must be stable and must still have accepted nothing.
    if (a3_accepted != 0 || a3_retired != 0 ||
        a3.scheduler_count != a3_held_count ||
        a3.scheduler_addr0 != a3_held_addr0 ||
        a3.scheduler_addr1 != a3_held_addr1)
      $fatal(1, "A3 disabled offer changed or was accepted");
    a3_enable = 1'b1;
    timeout = 0;
    while (!a3_drain && (timeout < 80)) begin
      @(posedge clk); #1;
      timeout = timeout + 1;
    end
    if (timeout >= 80)
      $fatal(1, "A3 endpoint failed to drain");
    if (a3_accepted != 7 || a3_retired != 7 || a3_head != a3_tail)
      $fatal(1, "A3 conservation mismatch accepted=%0d retired=%0d",
             a3_accepted, a3_retired);

    // Exercise the exact sticky-error cell instantiated by both wrappers.
    // The raw endpoint tests above prove sticky errors block clean drain.
    sticky_probe_event = 1'b1;
    #1;
    if (!sticky_probe_error)
      $fatal(1, "wrapper sticky-error cell was not immediate");
    @(posedge clk); #1;
    sticky_probe_event = 1'b0;
    #1;
    if (!sticky_probe_error)
      $fatal(1, "wrapper sticky-error cell did not retain history");

    rst = 1'b1;
    @(posedge clk); #1;
    a2_pending = 16'd0;
    a3_pending = 16'd0;
    if (sticky_probe_error || a2_error || a3_error ||
        a2_accept != 0 || a3_accept != 0 ||
        a2_retire_valid != 0 || a3_retire_valid != 0 ||
        !a2_drain || !a3_drain)
      $fatal(1, "integrated reset/drain was not quiet");

    $display("REDRED_SINGLE_EDGE_SMOKE_PASS legal_wire=257 raw_records=64 raw_events=96 A2=16 A3=7");
    $finish;
  end
endmodule
