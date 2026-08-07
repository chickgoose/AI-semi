`timescale 1ns/1ps

module a8_age_calendar_wheel_arbiter_tb;
  localparam int NUM_SOURCES = 4;
  localparam int BUCKET_CYCLES = 2;
  localparam int EPOCH_COUNT = 4;

  logic clk = 1'b0;
  logic rst_n = 1'b0;
  logic [NUM_SOURCES-1:0] request = '0;
  logic advance = 1'b1;
  logic [NUM_SOURCES-1:0] grant;
  logic [NUM_SOURCES-1:0] tracked;
  logic [$clog2(EPOCH_COUNT)-1:0] epoch;
  integer waited [NUM_SOURCES];
  integer source;
  integer max_wait;
  logic [NUM_SOURCES-1:0] served;
  logic [$clog2(EPOCH_COUNT)-1:0] captured_tag;

  always #5 clk = ~clk;

  a8_age_calendar_wheel_arbiter #(
    .NUM_SOURCES(NUM_SOURCES),
    .BUCKET_CYCLES(BUCKET_CYCLES),
    .EPOCH_COUNT(EPOCH_COUNT)
  ) dut (
    .clk(clk),
    .rst_n(rst_n),
    .request(request),
    .advance(advance),
    .grant(grant),
    .tracked_debug(tracked),
    .epoch_debug(epoch)
  );

  task automatic step;
    begin
      @(posedge clk);
      #1;
    end
  endtask

  task automatic expect_grant(input logic [NUM_SOURCES-1:0] expected,
                              input string reason);
    begin
      if (grant !== expected)
        $fatal(1, "%s expected grant=%b actual=%b", reason, expected, grant);
    end
  endtask

  initial begin
    repeat (2) step();
    rst_n = 1'b1;
    #1;

    // Same-cycle arrival is immediately eligible: no empty-calendar bubble.
    request = 4'b0100;
    #1;
    expect_grant(4'b0100, "same-cycle work conservation");
    step();
    request = '0;
    step();

    // Held valid before ready is tagged once and is not rejuvenated.
    advance = 1'b0;
    request = 4'b0001;
    step();
    if (!tracked[0])
      $fatal(1, "held request was not captured");
    captured_tag = dut.tag[0];
    repeat (3) step();
    if (dut.tag[0] != captured_tag)
      $fatal(1, "held request tag was rejuvenated");
    request[1] = 1'b1;
    step();
    advance = 1'b1;
    #1;
    expect_grant(4'b0001, "older held request must win");
    step();
    request[0] = 1'b0;
    #1;
    expect_grant(4'b0010, "newer request follows older");
    step();
    request = '0;
    step();

    // Capture before wrap and after wrap; the live pre-wrap tag remains older.
    while (epoch != EPOCH_COUNT-1)
      step();
    advance = 1'b0;
    request = 4'b0100;
    step();
    while (epoch != 0)
      step();
    request[3] = 1'b1;
    step();
    advance = 1'b1;
    #1;
    expect_grant(4'b0100, "modulo boundary ordering");
    step();
    request[2] = 1'b0;
    #1;
    expect_grant(4'b1000, "post-wrap request follows");
    step();
    request = '0;
    step();

    // Simultaneous requests drain work-conservingly with wait <= N-1.
    request = '1;
    max_wait = 0;
    for (source = 0; source < NUM_SOURCES; source = source + 1)
      waited[source] = 0;
    repeat (NUM_SOURCES) begin
      #1;
      if (!$onehot(grant))
        $fatal(1, "simultaneous drain must grant exactly one: %b", grant);
      for (source = 0; source < NUM_SOURCES; source = source + 1) begin
        if (request[source] && !grant[source]) begin
          waited[source] = waited[source] + 1;
          if (waited[source] > max_wait)
            max_wait = waited[source];
        end
      end
      served = grant;
      step();
      request = request & ~served;
    end
    if (request != '0)
      $fatal(1, "simultaneous requests did not drain");
    if (max_wait > NUM_SOURCES-1)
      $fatal(1, "bounded wait violated max=%0d", max_wait);

    $display("A8_WHEEL_UNIT_PASS max_wait=%0d", max_wait);
    $finish;
  end
endmodule
