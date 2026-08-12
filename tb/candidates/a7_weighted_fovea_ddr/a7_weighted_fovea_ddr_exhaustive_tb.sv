`timescale 1ns/1ps

// Canonical N16 exhaustive bitmap proof.  Each of the 65,535 non-empty live
// sets is presented until exactly one legal source handshake completes; zero
// is checked for quiescence.  One transaction is drained before the next set,
// so expected identity never depends on the canonical arbitration sequence.
module a7_weighted_fovea_ddr_exhaustive_tb;
  localparam time HALF = 8ns;
  logic ref_clk_i, sample_clk_i, rst_n;
  logic [15:0] source_valid, source_ready;
  logic burst_clk_o;
  logic [1:0] burst_data_o;
  logic [3:0] retire_addr_o;
  logic retire_valid_o, drain_idle_o, protocol_fault_o;
  logic consumer_valid_q;
  logic [3:0] consumer_addr_q;
  integer accepted, available, retired, ref_cycle;
  integer accept_cycle, output_cycle;
  logic [3:0] expected_addr;
  logic [15:0] current_bitmap;
  integer events_fd;
  string events_csv_path;

  a7_weighted_fovea_ddr dut (.*);

  initial begin ref_clk_i = 1'b0; forever #(HALF) ref_clk_i = ~ref_clk_i; end
  initial begin
    sample_clk_i = 1'b0;
    #12ns sample_clk_i = 1'b1;
    forever #(HALF) sample_clk_i = ~sample_clk_i;
  end

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
    integer lane;
    if (rst_n) begin
      ref_cycle = ref_cycle + 1;
      if (!$onehot0(source_ready))
        $fatal(1, "A7_W7_EXHAUSTIVE_READY_NOT_ONEHOT mask=%h ready=%h",
               source_valid, source_ready);
      if (|source_ready) begin
        if (accepted != retired)
          $fatal(1, "A7_W7_EXHAUSTIVE_OVERWRITE accepted=%0d retired=%0d", accepted, retired);
        for (lane = 0; lane < 16; lane = lane + 1)
          if (source_ready[lane]) expected_addr = lane[3:0];
        if (!source_valid[expected_addr])
          $fatal(1, "A7_W7_EXHAUSTIVE_ACK_NOT_LIVE addr=%0d mask=%h", expected_addr, source_valid);
        accept_cycle = ref_cycle;
        accepted = accepted + 1;
      end
      #1ps;
      if (retire_valid_o) begin
        if (available + 1 != accepted || retire_addr_o !== expected_addr ||
            ref_cycle != accept_cycle + 1)
          $fatal(1, "A7_W7_EXHAUSTIVE_OUTPUT_MISMATCH got=%h expected=%h cycle=%0d accept=%0d",
                 retire_addr_o, expected_addr, ref_cycle, accept_cycle);
        available = available + 1;
        output_cycle = ref_cycle;
      end
      if (consumer_valid_q) begin
        if (retired + 1 != accepted || consumer_addr_q !== expected_addr ||
            ref_cycle != accept_cycle + 2)
          $fatal(1, "A7_W7_EXHAUSTIVE_CONSUMER_MISMATCH got=%h expected=%h cycle=%0d accept=%0d",
                 consumer_addr_q, expected_addr, ref_cycle, accept_cycle);
        $fwrite(events_fd, "%0d,%0d,%0d,%0d,%0d,%0d\n",
                current_bitmap, expected_addr, consumer_addr_q,
                accept_cycle, output_cycle, ref_cycle);
        retired = retired + 1;
      end
      if (protocol_fault_o)
        $fatal(1, "A7_W7_EXHAUSTIVE_PROTOCOL_FAULT mask=%h", source_valid);
      if (!dut.endpoint_drain_idle && drain_idle_o)
        $fatal(1, "A7_W7_EXHAUSTIVE_DRAIN_ESCAPE edge=ref");
    end
  end

  always @(posedge burst_clk_o)
    if (rst_n && !dut.endpoint_drain_idle && drain_idle_o)
      $fatal(1, "A7_W7_EXHAUSTIVE_DRAIN_ESCAPE edge=burst_rise");
  always @(negedge burst_clk_o)
    if (rst_n && !dut.endpoint_drain_idle && drain_idle_o)
      $fatal(1, "A7_W7_EXHAUSTIVE_DRAIN_ESCAPE edge=burst_fall");

  task automatic wait_drain;
    integer timeout;
    begin
      timeout = 0;
      while ((!drain_idle_o || accepted != retired || retire_valid_o ||
              consumer_valid_q) && timeout < 16) begin
        @(negedge ref_clk_i);
        timeout = timeout + 1;
      end
      if (timeout == 16)
        $fatal(1, "A7_W7_EXHAUSTIVE_DRAIN_TIMEOUT accepted=%0d retired=%0d", accepted, retired);
    end
  endtask

  task automatic run_mask(input logic [15:0] mask);
    integer timeout;
    logic [15:0] selected;
    begin
      wait_drain();
      current_bitmap = mask;
      source_valid = mask;
      timeout = 0;
      while (!(|source_ready) && timeout < 8) begin
        @(negedge ref_clk_i);
        timeout = timeout + 1;
      end
      if (timeout == 8)
        $fatal(1, "A7_W7_EXHAUSTIVE_GRANT_TIMEOUT mask=%h", mask);
      selected = source_ready;
      if (!$onehot(selected) || (selected & mask) != selected)
        $fatal(1, "A7_W7_EXHAUSTIVE_ILLEGAL_SELECTION mask=%h ready=%h", mask, selected);
      // Keep exactly the selected transaction live across its handshake.  The
      // current-result mask makes canonical req zero on the accepting edge.
      source_valid = selected;
      @(posedge ref_clk_i);
      #1ps source_valid = '0;
      wait_drain();
    end
  endtask

  initial begin
    integer bitmap;
    rst_n = 1'b0;
    source_valid = '0;
    accepted = 0;
    available = 0;
    retired = 0;
    ref_cycle = 0;
    accept_cycle = -1;
    output_cycle = -1;
    expected_addr = '0;
    current_bitmap = '0;
    if (!$value$plusargs("A7_W7_EVENTS_CSV=%s", events_csv_path))
      $fatal(1, "A7_W7_EVIDENCE_CSV_PATH_REQUIRED");
    events_fd = $fopen(events_csv_path, "w");
    if (events_fd == 0)
      $fatal(1, "A7_W7_EVIDENCE_CSV_OPEN_FAILED path=%s", events_csv_path);
    $fwrite(events_fd,
            "bitmap,logical_source,retire_addr,accept_cycle,output_cycle,consumer_cycle\n");
    repeat (3) @(negedge sample_clk_i);
    rst_n = 1'b1;
    repeat (2) @(posedge ref_clk_i);
    #1ps;
    if (!dut.endpoint_ready)
      $fatal(1, "A7_W7_EXHAUSTIVE_RESET_ARM_FAIL");

    // Empty bitmap is the 65,536th case and must remain quiet.
    repeat (3) begin
      @(posedge ref_clk_i); #1ps;
      if (source_ready != '0 || retire_valid_o || protocol_fault_o)
        $fatal(1, "A7_W7_EXHAUSTIVE_ZERO_BITMAP_NOT_QUIET");
    end

    for (bitmap = 1; bitmap < 65536; bitmap = bitmap + 1)
      run_mask(bitmap[15:0]);

    if (accepted != 65535 || available != 65535 || retired != 65535)
      $fatal(1, "A7_W7_EXHAUSTIVE_COUNT_MISMATCH accepted=%0d available=%0d retired=%0d",
             accepted, available, retired);
    $fclose(events_fd);
    $display("A7_W7_N16_BITMAP_EXHAUSTIVE_PASS bitmaps=65536 nonempty=65535 accepted=65535 retired=65535");
    $finish;
  end
endmodule
