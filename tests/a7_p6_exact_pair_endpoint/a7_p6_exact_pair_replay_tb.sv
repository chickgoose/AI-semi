`timescale 1ns/1ps

module a7_p6_exact_pair_replay_tb;
  logic ref_clk = 1'b0;
  logic sample_clk = 1'b0;
  logic rst_n = 1'b0;
  logic input_valid = 1'b0;
  logic [1:0] input_count = 2'd0;
  logic [3:0] input_addr0 = 4'd0;
  logic [3:0] input_addr1 = 4'd0;
  logic input_ready, input_error;
  logic p6_clk;
  logic [4:0] p6_data;
  logic [1:0] retire_valid;
  logic [3:0] retire_addr0, retire_addr1;
  logic retire_error, drain_idle;

  integer bundle_fd;
  integer observed_fd;
  integer scan_status;
  integer next_cycle;
  integer next_count;
  integer next_addr0;
  integer next_addr1;
  integer replay_cycle = 0;
  integer monitor_cycle = 0;
  integer replay_origin_monitor = 0;
  integer head = 0;
  integer tail = 0;
  integer retired_records = 0;
  integer expected_count [0:99999];
  integer expected_addr0 [0:99999];
  integer expected_addr1 [0:99999];
  logic have_next = 1'b0;
  logic replay_active = 1'b0;
  logic replay_done = 1'b0;
  logic sampled_fire;
  string bundle_path;
  string observed_path;

  always #8 ref_clk = ~ref_clk;
  initial begin
    #4;
    forever #8 sample_clk = ~sample_clk;
  end

  a7_p6_exact_pair_endpoint dut (
    .ref_clk_i(ref_clk), .sample_clk_i(sample_clk), .rst_n,
    .input_valid_i(input_valid), .input_count_i(input_count),
    .input_addr0_i(input_addr0), .input_addr1_i(input_addr1),
    .input_ready_o(input_ready), .input_protocol_error_o(input_error),
    .p6_clk_o(p6_clk), .p6_data_o(p6_data), .retire_valid_o(retire_valid),
    .retire_addr0_o(retire_addr0), .retire_addr1_o(retire_addr1),
    .retire_protocol_error_o(retire_error), .drain_idle_o(drain_idle)
  );

  task automatic read_next;
    begin
      scan_status = $fscanf(bundle_fd, "%d %d %x %x\n",
                            next_cycle, next_count, next_addr0, next_addr1);
      have_next = (scan_status == 4);
      if (!have_next)
        replay_done = 1'b1;
    end
  endtask

  always @(posedge ref_clk) begin
    sampled_fire = input_valid && input_ready;
    monitor_cycle = monitor_cycle + 1;
    #1;
    if (input_error || retire_error)
      $fatal(1, "A7_P6_REPLAY_FAIL protocol error");
    if (retire_valid != 0) begin
      if (head == tail)
        $fatal(1, "A7_P6_REPLAY_FAIL phantom retirement");
      if (retire_valid !== ((expected_count[head] == 2) ? 2'b11 : 2'b01) ||
          retire_addr0 !== expected_addr0[head][3:0] ||
          retire_addr1 !== expected_addr1[head][3:0])
        $fatal(1, "A7_P6_REPLAY_FAIL ordered data mismatch record=%0d", head);
      $fwrite(observed_fd, "%0d,%0d,%0d,%x,%x\n", retired_records,
              monitor_cycle-replay_origin_monitor-1, expected_count[head],
              retire_addr0, retire_addr1);
      retired_records = retired_records + 1;
      head = head + 1;
    end
    if (sampled_fire) begin
      expected_count[tail] = input_count;
      expected_addr0[tail] = input_addr0;
      expected_addr1[tail] = (input_count == 2) ? input_addr1 : 0;
      tail = tail + 1;
    end
  end

  always @(negedge ref_clk) begin
    if (replay_active) begin
      input_valid = 1'b0;
      input_count = 2'd0;
      input_addr0 = 4'd0;
      input_addr1 = 4'd0;
      if (have_next && next_cycle == replay_cycle) begin
        input_valid = 1'b1;
        input_count = next_count[1:0];
        input_addr0 = next_addr0[3:0];
        input_addr1 = next_addr1[3:0];
        read_next();
      end else if (have_next && next_cycle < replay_cycle) begin
        $fatal(1, "A7_P6_REPLAY_FAIL missed input cycle=%0d now=%0d",
               next_cycle, replay_cycle);
      end
      replay_cycle = replay_cycle + 1;
    end
  end

  initial begin
    if (!$value$plusargs("BUNDLE=%s", bundle_path))
      $fatal(1, "A7_P6_REPLAY_FAIL missing BUNDLE plusarg");
    if (!$value$plusargs("OBSERVED=%s", observed_path))
      $fatal(1, "A7_P6_REPLAY_FAIL missing OBSERVED plusarg");
    bundle_fd = $fopen(bundle_path, "r");
    observed_fd = $fopen(observed_path, "w");
    if ((bundle_fd == 0) || (observed_fd == 0))
      $fatal(1, "A7_P6_REPLAY_FAIL could not open replay files");
    read_next();
    repeat (3) @(posedge ref_clk);
    @(negedge ref_clk);
    rst_n = 1'b1;
    repeat (2) @(posedge ref_clk);
    @(negedge ref_clk);
    replay_origin_monitor = monitor_cycle;
    replay_active = 1'b1;
    while (!replay_done || input_valid)
      @(posedge ref_clk);
    @(negedge ref_clk);
    replay_active = 1'b0;
    input_valid = 1'b0;
    input_count = 2'd0;
    while (!drain_idle || head != tail)
      @(posedge ref_clk);
    repeat (2) @(posedge ref_clk);
    $fclose(bundle_fd);
    $fclose(observed_fd);
    $display("A7_P6_FROZEN_REPLAY_PASS records=%0d queue_state_bits=0", retired_records);
    $finish;
  end
endmodule
