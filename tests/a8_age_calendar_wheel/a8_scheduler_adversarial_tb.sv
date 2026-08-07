`timescale 1ns/1ps

module a8_scheduler_adversarial_tb;
  localparam int N = 4;
  logic clk = 1'b0;
  logic rst_n = 1'b0;

  logic [N-1:0] compare_request = '0;
  logic compare_advance = 1'b1;
  logic [N-1:0] b1_grant;
  logic [N-1:0] exact_grant;
  logic [N-1:0] compare_tracked_unused;
  logic [3:0] compare_epoch;

  logic [N-1:0] collision_request = '0;
  logic collision_advance = 1'b1;
  logic [N-1:0] b4_grant;
  logic [N-1:0] collision_exact_grant;
  logic [N-1:0] collision_tracked_unused;
  logic [1:0] collision_epoch;

  integer wait_count [N];
  integer max_wait;
  integer source;
  integer stall_cycle;
  logic [N-1:0] served;

  always #5 clk = ~clk;

  a8_age_calendar_wheel_arbiter #(
    .NUM_SOURCES(N), .BUCKET_CYCLES(1), .EPOCH_COUNT(16),
    .MAX_STALL_CYCLES(8)
  ) b1 (
    .clk(clk), .rst_n(rst_n), .request(compare_request),
    .advance(compare_advance), .grant(b1_grant),
    .tracked_debug(compare_tracked_unused), .epoch_debug(compare_epoch)
  );

  a8_exact_age_reference_arbiter #(
    .NUM_SOURCES(N), .AGE_WIDTH(4)
  ) exact (
    .clk(clk), .rst_n(rst_n), .request(compare_request),
    .advance(compare_advance), .grant(exact_grant),
    .tracked_debug()
  );

  a8_age_calendar_wheel_arbiter #(
    .NUM_SOURCES(N), .BUCKET_CYCLES(4), .EPOCH_COUNT(4),
    .MAX_STALL_CYCLES(8)
  ) collision (
    .clk(clk), .rst_n(rst_n), .request(collision_request),
    .advance(collision_advance), .grant(b4_grant),
    .tracked_debug(collision_tracked_unused), .epoch_debug(collision_epoch)
  );

  a8_exact_age_reference_arbiter #(
    .NUM_SOURCES(N), .AGE_WIDTH(4)
  ) collision_exact (
    .clk(clk), .rst_n(rst_n), .request(collision_request),
    .advance(collision_advance), .grant(collision_exact_grant),
    .tracked_debug()
  );

  task automatic step;
    begin
      @(posedge clk);
      #1;
    end
  endtask

  task automatic compare_exact(input string reason);
    begin
      #1;
      if (b1_grant !== exact_grant)
        $fatal(1, "%s B1=%b exact=%b", reason, b1_grant, exact_grant);
    end
  endtask

  initial begin
    repeat (2) step();
    rst_n = 1'b1;
    step();

    // Put first-seen time immediately before modulo wrap, then introduce a
    // younger request during a bounded continuous stall.
    while (compare_epoch != 4'd14)
      step();
    compare_advance = 1'b0;
    compare_request[0] = 1'b1;
    step();
    step();
    compare_request[1] = 1'b1;
    step();
    compare_advance = 1'b1;
    compare_exact("adversarial wrap oldest selection");
    if (b1_grant != 4'b0001)
      $fatal(1, "pre-wrap request did not beat post-wrap request");
    served = b1_grant;
    step();
    compare_request = compare_request & ~served;
    compare_exact("adversarial wrap second selection");
    if (b1_grant != 4'b0010)
      $fatal(1, "post-wrap request was not second");
    served = b1_grant;
    step();
    compare_request = compare_request & ~served;

    // Eight consecutive stalled cycles plus at most N-1 services stays below
    // the 16-cycle horizon. B1 must remain cycle-exact with the reference.
    for (source = 0; source < N; source = source + 1)
      wait_count[source] = 0;
    max_wait = 0;
    compare_advance = 1'b0;
    compare_request[2] = 1'b1;
    for (stall_cycle = 0; stall_cycle < 8; stall_cycle = stall_cycle + 1) begin
      if (stall_cycle == 2)
        compare_request[3] = 1'b1;
      if (stall_cycle == 4)
        compare_request[0] = 1'b1;
      for (source = 0; source < N; source = source + 1)
        if (compare_request[source]) begin
          wait_count[source] = wait_count[source] + 1;
          if (wait_count[source] > max_wait)
            max_wait = wait_count[source];
        end
      step();
      compare_exact("continuous bounded stall");
    end
    compare_advance = 1'b1;
    while (compare_request != '0) begin
      compare_exact("post-stall exact drain");
      if (!$onehot(b1_grant))
        $fatal(1, "post-stall drain was not work-conserving");
      served = b1_grant;
      for (source = 0; source < N; source = source + 1)
        if (compare_request[source] && !served[source]) begin
          wait_count[source] = wait_count[source] + 1;
          if (wait_count[source] > max_wait)
            max_wait = wait_count[source];
        end
      step();
      compare_request = compare_request & ~served;
    end
    if (max_wait > 8 + N - 1)
      $fatal(1, "stall-bound wait exceeded proof max=%0d", max_wait);

    // Prime tie-start to source 2. Then place source 3 one cycle before source
    // 2 in the same four-cycle bucket. Exact age selects 3; B4 may select the
    // younger source 2, making the quantization loss explicit.
    collision_request[1] = 1'b1;
    #1;
    if (b4_grant != 4'b0010 || collision_exact_grant != 4'b0010)
      $fatal(1, "failed to prime collision tie pointer");
    step();
    collision_request = '0;
    while (collision.bucket_phase != 0)
      step();
    collision_advance = 1'b0;
    collision_request[3] = 1'b1;
    step();
    collision_request[2] = 1'b1;
    step();
    collision_advance = 1'b1;
    #1;
    if (collision_exact_grant != 4'b1000)
      $fatal(1, "exact reference did not preserve older collision request");
    if (b4_grant != 4'b0100)
      $fatal(1, "B4 collision did not expose expected younger-first tie");

    $display("A8_ADVERSARIAL_PASS stall_bound=8 max_wait=%0d collision_loss=1", max_wait);
    $finish;
  end
endmodule
