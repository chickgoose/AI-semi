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
  integer a2_head, a2_tail, a3_head, a3_tail;
  integer a2_accepted, a2_retired, a3_accepted, a3_retired;
  integer timeout;

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
    end else begin
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
    a2_enable = 1'b0;
    a3_enable = 1'b0;
    a2_pending = 16'd0;
    a3_pending = 16'd0;

    repeat (2) @(posedge clk);
    #1 rst = 1'b0;

    // Malformed K2 input fails closed and the error remains visible.
    raw_enable = 1'b1;
    raw_count = 2'd3;
    #1;
    if (raw_ready || raw_commit || !raw_error)
      $fatal(1, "illegal count did not fail closed");
    @(posedge clk); #1;
    raw_count = 2'd0;
    if (!raw_error)
      $fatal(1, "TX protocol_error was not sticky");

    // Reset clears protocol state and is externally quiet after its edge.
    rst = 1'b1;
    @(posedge clk); #1;
    if (raw_error || raw_link_valid || raw_retire_valid != 2'b00 || !raw_drain)
      $fatal(1, "synchronous reset did not flush the raw endpoint");
    rst = 1'b0;

    // Back-to-back singleton then pair: equal/equal and distinct encodings
    // must reconstruct the exact accepted identity and order.
    raw_count = 2'd1;
    raw_addr0 = 4'd5;
    raw_addr1 = 4'd0;
    #1;
    if (!raw_commit || raw_microsteps != 2'd1)
      $fatal(1, "singleton was not admitted atomically");
    @(posedge clk); #1;
    if (!raw_link_valid || raw_link_addr0 != 4'd5 || raw_link_addr1 != 4'd5)
      $fatal(1, "singleton link encoding mismatch");
    raw_count = 2'd2;
    raw_addr0 = 4'd2;
    raw_addr1 = 4'd9;
    @(posedge clk); #1;
    if (raw_retire_valid != 2'b01 || raw_retire_addr0 != 4'd5)
      $fatal(1, "singleton retirement mismatch");
    if (!raw_link_valid || raw_link_addr0 != 4'd2 || raw_link_addr1 != 4'd9)
      $fatal(1, "pair link encoding mismatch");
    raw_count = 2'd0;
    raw_addr0 = 4'd0;
    raw_addr1 = 4'd0;
    @(posedge clk); #1;
    if (raw_retire_valid != 2'b11 || raw_retire_addr0 != 4'd2 ||
        raw_retire_addr1 != 4'd9)
      $fatal(1, "ordered pair retirement mismatch");
    @(posedge clk); #1;
    if (raw_retire_valid != 2'b00 || !raw_drain)
      $fatal(1, "raw endpoint did not drain");

    // The RX exposes malformed idle payload and holds the indication.
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

    // Both real owners must hold a whole ordered offer while disabled, then
    // accept and retire every pending identity exactly once in FIFO order.
    a2_pending = 16'hffff;
    a3_pending = 16'hca61;
    repeat (3) @(posedge clk);
    #1;
    if (a2_accept_count != 0 || a3_accept_count != 0 ||
        a2_retire_valid != 0 || a3_retire_valid != 0)
      $fatal(1, "link disable admitted or retired work");
    a2_enable = 1'b1;
    a3_enable = 1'b1;

    timeout = 0;
    while ((!a2_drain || !a3_drain) && (timeout < 80)) begin
      @(posedge clk); #1;
      timeout = timeout + 1;
    end
    if (timeout >= 80)
      $fatal(1, "integrated endpoints failed to drain");
    if (a2_accepted != 16 || a2_retired != 16 || a2_head != a2_tail)
      $fatal(1, "A2 conservation mismatch accepted=%0d retired=%0d",
             a2_accepted, a2_retired);
    if (a3_accepted != 7 || a3_retired != 7 || a3_head != a3_tail)
      $fatal(1, "A3 conservation mismatch accepted=%0d retired=%0d",
             a3_accepted, a3_retired);

    // Drain-before-reset is lossless; after the sampled reset edge all
    // endpoint state is quiet and no stale retirement can escape.
    rst = 1'b1;
    @(posedge clk); #1;
    a2_pending = 16'd0;
    a3_pending = 16'd0;
    if (a2_accept != 0 || a3_accept != 0 || a2_retire_valid != 0 ||
        a3_retire_valid != 0 || !a2_drain || !a3_drain)
      $fatal(1, "integrated reset/drain was not quiet");

    $display("REDRED_SINGLE_EDGE_SMOKE_PASS A2=16 A3=7");
    $finish;
  end
endmodule
