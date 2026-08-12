`timescale 1ns/1ps

module a7_p6_exact_pair_lockstep_tb;
  logic ref_clk = 1'b0;
  logic sample_clk = 1'b0;
  logic rst_n = 1'b0;
  logic input_valid;
  logic [1:0] input_count;
  logic [3:0] input_addr0;
  logic [3:0] input_addr1;
  logic p6_ready, p6_input_error;
  logic p6_clk;
  logic [4:0] p6_data;
  logic [1:0] p6_retire_valid;
  logic [3:0] p6_retire_addr0, p6_retire_addr1;
  logic p6_retire_error, p6_drain;
  logic ref_ready, ref_input_error;
  logic ref_strobe, ref_pair;
  logic [3:0] ref_link_addr0, ref_link_addr1;
  logic [1:0] ref_retire_valid;
  logic [3:0] ref_retire_addr0, ref_retire_addr1;
  logic ref_retire_error, ref_drain;

  integer cycle = 0;
  integer accepted = 0;
  integer retired = 0;
  integer errors = 0;
  integer expected_head = 0;
  integer expected_tail = 0;
  integer link_head = 0;
  integer expected_link_word;
  integer expected_count [0:4095];
  integer expected_addr0 [0:4095];
  integer expected_addr1 [0:4095];
  integer expected_cycle [0:4095];
  logic sampled_fire;
  logic [4:0] observed_low_symbol;

  always #8 ref_clk = ~ref_clk;
  initial begin
    #4;
    forever #8 sample_clk = ~sample_clk;
  end

  a7_p6_exact_pair_endpoint dut (
    .ref_clk_i(ref_clk), .sample_clk_i(sample_clk), .rst_n,
    .input_valid_i(input_valid), .input_count_i(input_count),
    .input_addr0_i(input_addr0), .input_addr1_i(input_addr1),
    .input_ready_o(p6_ready), .input_protocol_error_o(p6_input_error),
    .p6_clk_o(p6_clk), .p6_data_o(p6_data),
    .retire_valid_o(p6_retire_valid), .retire_addr0_o(p6_retire_addr0),
    .retire_addr1_o(p6_retire_addr1),
    .retire_protocol_error_o(p6_retire_error), .drain_idle_o(p6_drain)
  );

  a7_p6_exact_pair_parallel_reference parallel_ref (
    .ref_clk_i(ref_clk), .sample_clk_i(sample_clk), .rst_n,
    .input_valid_i(input_valid), .input_count_i(input_count),
    .input_addr0_i(input_addr0), .input_addr1_i(input_addr1),
    .input_ready_o(ref_ready), .input_protocol_error_o(ref_input_error),
    .parallel_strobe_o(ref_strobe), .parallel_pair_o(ref_pair),
    .parallel_addr0_o(ref_link_addr0), .parallel_addr1_o(ref_link_addr1),
    .retire_valid_o(ref_retire_valid), .retire_addr0_o(ref_retire_addr0),
    .retire_addr1_o(ref_retire_addr1),
    .retire_protocol_error_o(ref_retire_error), .drain_idle_o(ref_drain)
  );

  task automatic fail(input string marker, input string reason);
    begin
      $display("%s %s", marker, reason);
      $fatal(1, "%s", reason);
    end
  endtask

  always @(posedge p6_clk) begin
    if (rst_n)
      observed_low_symbol = p6_data;
  end

  always @(negedge p6_clk) begin
    if (rst_n) begin
      if (link_head == expected_tail)
        fail("A7_P6_LINK_CODE_FAIL", "raw frame without accepted record");
      expected_link_word = ((expected_count[link_head] == 2) ? 10'h200 : 10'h000) |
                           (expected_addr0[link_head] << 4) |
                           ((expected_count[link_head] == 2) ?
                            expected_addr1[link_head] : 0);
      if ({p6_data, observed_low_symbol} !== expected_link_word[9:0])
        fail("A7_P6_LINK_CODE_FAIL", "raw P6 code word mismatch");
      link_head = link_head + 1;
    end
  end

  always @(posedge ref_clk) begin
    sampled_fire = input_valid && p6_ready;
    cycle = cycle + 1;
    #1;
    if (p6_ready !== ref_ready || p6_input_error !== ref_input_error)
      fail("A7_P6_LOCKSTEP_FAIL", "input contract mismatch");
    if ((p6_retire_valid != 2'b00) && (expected_head == expected_tail))
      fail("A7_P6_RESET_MUTATION_CAUGHT", "phantom retirement");
    if (p6_retire_valid !== ref_retire_valid ||
        p6_retire_addr0 !== ref_retire_addr0 ||
        p6_retire_addr1 !== ref_retire_addr1 ||
        p6_retire_error !== ref_retire_error)
      fail("A7_P6_ORDER_MUTATION_CAUGHT", "P6/parallel retirement mismatch");
    if (p6_retire_error || ref_retire_error)
      fail("A7_P6_LOCKSTEP_FAIL", "unexpected retire protocol error");
    if (p6_retire_valid != 2'b00) begin
      if (p6_retire_valid !== ((expected_count[expected_head] == 2) ? 2'b11 : 2'b01) ||
          p6_retire_addr0 !== expected_addr0[expected_head][3:0] ||
          p6_retire_addr1 !== expected_addr1[expected_head][3:0])
        fail("A7_P6_ORDER_MUTATION_CAUGHT", "ordered payload mismatch");
      if (cycle != expected_cycle[expected_head] + 1)
        fail("A7_P6_LOCKSTEP_FAIL", "nonconstant endpoint latency");
      expected_head = expected_head + 1;
      retired = retired + ((p6_retire_valid == 2'b11) ? 2 : 1);
    end
    if (sampled_fire) begin
      expected_count[expected_tail] = input_count;
      expected_addr0[expected_tail] = input_addr0;
      expected_addr1[expected_tail] = (input_count == 2) ? input_addr1 : 0;
      expected_cycle[expected_tail] = cycle;
      expected_tail = expected_tail + 1;
      accepted = accepted + input_count;
    end
  end

  task automatic drive(input logic valid, input logic [1:0] count,
                       input logic [3:0] addr0, input logic [3:0] addr1);
    begin
      @(negedge ref_clk);
      input_valid = valid;
      input_count = count;
      input_addr0 = addr0;
      input_addr1 = addr1;
    end
  endtask

  task automatic wait_drain;
    begin
      drive(1'b0, 2'd0, 4'd0, 4'd0);
      while (!p6_drain || !ref_drain || expected_head != expected_tail)
        @(posedge ref_clk);
      @(negedge ref_clk);
    end
  endtask

  initial begin
    input_valid = 1'b0;
    input_count = 2'd0;
    input_addr0 = '0;
    input_addr1 = '0;

    // Held transaction during reset and the charged release-arm interval.
    drive(1'b1, 2'd2, 4'hd, 4'h2);
    repeat (2) @(posedge ref_clk);
    if (p6_ready || ref_ready)
      fail("A7_P6_STALL_MUTATION_CAUGHT", "ready asserted during reset");
    @(negedge ref_clk);
    rst_n = 1'b1;
    if (p6_ready || ref_ready)
      fail("A7_P6_STALL_MUTATION_CAUGHT", "ready asserted before arm edge");
    @(posedge ref_clk);
    if (sampled_fire)
      fail("A7_P6_STALL_MUTATION_CAUGHT", "transaction accepted on arm edge");
    @(posedge ref_clk);

    // Exhaust all 16 singleton and 256 ordered-pair code words in RTL.
    for (integer first = 0; first < 16; first = first + 1)
      drive(1'b1, 2'd1, first[3:0], 4'hf);
    for (integer first = 0; first < 16; first = first + 1)
      for (integer second = 0; second < 16; second = second + 1)
        drive(1'b1, 2'd2, first[3:0], second[3:0]);

    // Additional back-to-back traffic and idle insertion.
    for (integer index = 0; index < 512; index = index + 1) begin
      if ((index % 7) == 0)
        drive(1'b0, 2'd0, 4'd0, 4'd0);
      else if ((index % 3) == 0)
        drive(1'b1, 2'd1, index[3:0], 4'hf);
      else
        drive(1'b1, 2'd2, index[3:0], 4'(15-index));
    end
    wait_drain();

    // Count 3 is an attempted overflow of the frozen two-address contract.
    drive(1'b1, 2'd3, 4'h1, 4'he);
    #1;
    if (!p6_input_error || p6_ready)
      fail("A7_P6_OVERFLOW_MUTATION_CAUGHT", "illegal count did not fail closed");
    repeat (2) @(posedge ref_clk);
    drive(1'b0, 2'd0, 4'd0, 4'd0);
    wait_drain();

    // Legal drain-reset-rearm must not leak an old frame or toggle.
    @(negedge ref_clk);
    rst_n = 1'b0;
    repeat (2) @(posedge ref_clk);
    if (p6_retire_valid != 0)
      fail("A7_P6_RESET_MUTATION_CAUGHT", "retirement active in reset");
    @(negedge ref_clk);
    rst_n = 1'b1;
    repeat (2) @(posedge ref_clk);
    drive(1'b1, 2'd2, 4'h3, 4'hc);
    @(posedge ref_clk);
    drive(1'b0, 2'd0, 4'd0, 4'd0);
    wait_drain();

    if (accepted != retired || expected_head != expected_tail ||
        link_head != expected_tail || errors != 0)
      $fatal(1, "A7_P6_LOCKSTEP_FAIL accepted=%0d retired=%0d pending=%0d",
             accepted, retired, expected_tail-expected_head);
    $display("A7_P6_LOCKSTEP_PASS accepted=%0d retired=%0d records=%0d queue_state_bits=0",
             accepted, retired, expected_tail);
    $finish;
  end
endmodule
