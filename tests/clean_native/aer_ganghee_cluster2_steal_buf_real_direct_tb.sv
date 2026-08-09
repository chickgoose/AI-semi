`timescale 1ns/1ps

module aer_ganghee_cluster2_steal_buf_real_direct_tb;
  logic clk = 1'b0;
  logic rst;
  logic [15:0] arrival;
  wire [15:0] overrun;
  wire valid0;
  wire [1:0] row0;
  wire [3:0] col_mask0;
  wire valid1;
  wire [1:0] row1;
  wire [3:0] col_mask1;
  logic [15:0] decoded0;
  logic [15:0] decoded1;
  logic [15:0] decoded;
  logic [15:0] sampled_overrun;
  logic [15:0] post_edge_overrun;
  integer reference_count [0:15];
  integer generated;
  integer admitted;
  integer dropped;
  integer delivered;
  integer errors = 0;
  integer cycle_number;
  integer source_index;
  integer post_edge_false_full;
  integer post_edge_hidden_full;

  always #5 clk = ~clk;

  aer_tx16_trad_rowcol_fovea_cluster2_steal_buf dut (
    .clk(clk),
    .rst(rst),
    .arrival(arrival),
    .overrun(overrun),
    .valid0(valid0),
    .row0(row0),
    .col_mask0(col_mask0),
    .valid1(valid1),
    .row1(row1),
    .col_mask1(col_mask1)
  );

  always_comb begin
    decoded0 = 16'b0;
    decoded1 = 16'b0;
    if (valid0)
      decoded0[(integer'(row0) * 4) +: 4] = col_mask0;
    if (valid1)
      decoded1[(integer'(row1) * 4) +: 4] = col_mask1;
    decoded = decoded0 | decoded1;
  end

  task automatic clear_accounting;
    begin
      generated = 0;
      admitted = 0;
      dropped = 0;
      delivered = 0;
      cycle_number = 0;
      post_edge_false_full = 0;
      post_edge_hidden_full = 0;
      for (source_index = 0; source_index < 16; source_index = source_index + 1)
        reference_count[source_index] = 0;
    end
  endtask

  task automatic reset_dut;
    begin
      @(negedge clk);
      rst = 1'b1;
      arrival = 16'b0;
      repeat (2) @(posedge clk);
      #1;
      if (decoded != 16'b0) begin
        errors = errors + 1;
        $error("reset produced output=%h", decoded);
      end
      clear_accounting();
      @(negedge clk);
      rst = 1'b0;
    end
  endtask

  task automatic pulse_and_sample(input logic [15:0] pulse_bits,
                                  input string phase_text);
    logic [15:0] expected_overrun;
    integer old_count;
    logic accepted_bit;
    logic grant_bit;
    begin
      @(negedge clk);
      arrival = pulse_bits;
      expected_overrun = 16'b0;
      for (source_index = 0; source_index < 16; source_index = source_index + 1)
        if (pulse_bits[source_index] && reference_count[source_index] == 2)
          expected_overrun[source_index] = 1'b1;

      @(posedge clk);
      sampled_overrun = overrun;
      if (sampled_overrun !== expected_overrun) begin
        errors = errors + 1;
        $error("%s cycle=%0d edge overrun expected=%h actual=%h",
               phase_text, cycle_number, expected_overrun, sampled_overrun);
      end
      generated += $countones(pulse_bits);
      dropped += $countones(sampled_overrun);
      admitted += $countones(pulse_bits & ~sampled_overrun);

      #1;
      post_edge_overrun = overrun;
      post_edge_false_full += $countones(post_edge_overrun & ~sampled_overrun);
      post_edge_hidden_full += $countones(sampled_overrun & ~post_edge_overrun);

      if ((decoded0 & decoded1) != 16'b0) begin
        errors = errors + 1;
        $error("%s cycle=%0d lane overlap lane0=%h lane1=%h",
               phase_text, cycle_number, decoded0, decoded1);
      end

      for (source_index = 0; source_index < 16; source_index = source_index + 1) begin
        old_count = reference_count[source_index];
        accepted_bit = pulse_bits[source_index] && !sampled_overrun[source_index];
        grant_bit = decoded[source_index];
        if (grant_bit && old_count == 0) begin
          errors = errors + 1;
          $error("%s cycle=%0d source=%0d output without stored arrival",
                 phase_text, cycle_number, source_index);
        end
        reference_count[source_index] = old_count + integer'(accepted_bit) -
                                        integer'(grant_bit);
        if ((reference_count[source_index] < 0) ||
            (reference_count[source_index] > 2)) begin
          errors = errors + 1;
          $error("%s cycle=%0d source=%0d invalid reference count=%0d",
                 phase_text, cycle_number, source_index,
                 reference_count[source_index]);
        end
      end
      delivered += $countones(decoded);
      $display("%s cycle=%0d arrival=%h edge_overrun=%h post_overrun=%h decoded=%h v0=%b r0=%0d m0=%h v1=%b r1=%0d m1=%h",
               phase_text, cycle_number, pulse_bits, sampled_overrun,
               post_edge_overrun, decoded,
               valid0, row0, col_mask0, valid1, row1, col_mask1);
      cycle_number += 1;
    end
  endtask

  task automatic check_drained(input string phase_text,
                               input integer expected_generated,
                               input integer expected_dropped);
    begin
      if (generated != expected_generated || dropped != expected_dropped ||
          admitted != delivered || generated != admitted + dropped) begin
        errors = errors + 1;
        $error("%s accounting generated=%0d admitted=%0d dropped=%0d delivered=%0d",
               phase_text, generated, admitted, dropped, delivered);
      end
      for (source_index = 0; source_index < 16; source_index = source_index + 1)
        if (reference_count[source_index] != 0) begin
          errors = errors + 1;
          $error("%s source=%0d did not drain count=%0d",
                 phase_text, source_index, reference_count[source_index]);
        end
      $display("%s_SUMMARY generated=%0d admitted=%0d overrun=%0d delivered=%0d post_false_full=%0d post_hidden_full=%0d",
               phase_text, generated, admitted, dropped, delivered,
               post_edge_false_full, post_edge_hidden_full);
    end
  endtask

  initial begin
    rst = 1'b1;
    arrival = 16'b0;

    // An isolated source may re-fire once per cycle: after the initial fill,
    // one arrival and one output cancel at every edge.
    reset_dut();
    repeat (4) pulse_and_sample(16'h0010, "REPEAT_ONE_SOURCE");
    repeat (2) pulse_and_sample(16'h0000, "REPEAT_ONE_SOURCE_DRAIN");
    check_drained("REPEAT_ONE_SOURCE", 4, 0);

    // A peripheral row occupies the second native slot, so the two center rows
    // compete for the remaining slot and force depth-two loss.
    reset_dut();
    repeat (8) pulse_and_sample(16'h0111, "FULL_CONTENTION");
    repeat (4) pulse_and_sample(16'h0000, "FULL_CONTENTION_DRAIN");
    check_drained("FULL_CONTENTION", 24, 6);
    if (post_edge_false_full == 0 || post_edge_hidden_full == 0) begin
      errors = errors + 1;
      $error("full test did not expose edge-sampling hazard false=%0d hidden=%0d",
             post_edge_false_full, post_edge_hidden_full);
    end

    if (errors == 0)
      $display("GANGHEE_CLUSTER2_STEAL_BUF_REAL_DIRECT_PASS");
    else
      $fatal(1, "GANGHEE_CLUSTER2_STEAL_BUF_REAL_DIRECT_FAIL errors=%0d", errors);
    $finish;
  end
endmodule
