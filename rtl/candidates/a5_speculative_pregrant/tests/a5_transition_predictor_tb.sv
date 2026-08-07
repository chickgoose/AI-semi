`timescale 1ns/1ps

module a5_transition_predictor_tb;
  localparam int NUM_SOURCES = 16;
  localparam int SOURCE_WIDTH = 4;

  logic clk = 1'b0;
  logic rst_n;
  logic lookup_valid;
  logic [SOURCE_WIDTH-1:0] lookup_context;
  logic prediction_valid;
  logic [SOURCE_WIDTH-1:0] prediction_target;
  logic [1:0] prediction_confidence;
  logic update_valid;
  logic [SOURCE_WIDTH-1:0] update_context;
  logic [SOURCE_WIDTH-1:0] update_actual;

  always #5 clk = ~clk;

  a5_transition_predictor #(
    .NUM_SOURCES(NUM_SOURCES),
    .SOURCE_WIDTH(SOURCE_WIDTH)
  ) dut (
    .clk,
    .rst_n,
    .lookup_valid,
    .lookup_context,
    .prediction_valid,
    .prediction_target,
    .prediction_confidence,
    .update_valid,
    .update_context,
    .update_actual
  );

  task automatic train(input int context_id, input int actual_id);
    begin
      @(negedge clk);
      update_valid = 1'b1;
      update_context = SOURCE_WIDTH'(context_id);
      update_actual = SOURCE_WIDTH'(actual_id);
      @(negedge clk);
      update_valid = 1'b0;
    end
  endtask

  task automatic expect_lookup(
    input int context_id,
    input logic expected_valid,
    input int expected_target,
    input int expected_confidence
  );
    begin
      lookup_valid = 1'b1;
      lookup_context = SOURCE_WIDTH'(context_id);
      #1;
      if ((prediction_valid !== expected_valid) ||
          (prediction_target !== SOURCE_WIDTH'(expected_target)) ||
          (prediction_confidence !== 2'(expected_confidence)))
        $fatal(1,
          "lookup context=%0d got valid=%0b target=%0d conf=%0d expected valid=%0b target=%0d conf=%0d",
          context_id, prediction_valid, prediction_target, prediction_confidence,
          expected_valid, expected_target, expected_confidence);
    end
  endtask

  initial begin
    rst_n = 1'b0;
    lookup_valid = 1'b0;
    lookup_context = '0;
    update_valid = 1'b0;
    update_context = '0;
    update_actual = '0;
    repeat (2) @(negedge clk);
    rst_n = 1'b1;

    // Cold entries never authorize speculation.
    expect_lookup(3, 1'b0, 0, 0);

    // A->B and B->A are separate context entries, not a last-winner rule.
    train(3, 9);
    expect_lookup(3, 1'b0, 9, 1);
    train(3, 9);
    expect_lookup(3, 1'b1, 9, 2);
    train(9, 3);
    train(9, 3);
    expect_lookup(9, 1'b1, 3, 2);

    // One anomalous successor removes confidence but does not instantly retarget.
    train(3, 5);
    expect_lookup(3, 1'b0, 9, 1);
    train(3, 5);
    expect_lookup(3, 1'b0, 9, 0);
    train(3, 5);
    expect_lookup(3, 1'b0, 5, 1);
    train(3, 5);
    expect_lookup(3, 1'b1, 5, 2);

    // Reset removes every learned target and confidence bit.
    @(negedge clk);
    rst_n = 1'b0;
    @(negedge clk);
    rst_n = 1'b1;
    expect_lookup(3, 1'b0, 0, 0);
    expect_lookup(9, 1'b0, 0, 0);

    $display("A5_TRANSITION_PREDICTOR_PASS");
    $finish;
  end
endmodule
