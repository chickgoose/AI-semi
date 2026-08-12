module a2_batched_iwrr_k2_lockstep_tb;
  logic clk = 1'b0;
  logic rst;
  logic [15:0] req;
  logic [1:0] grant_count;
  logic [3:0] grant_addr0, grant_addr1;
  logic [15:0] grant_bitmap;
  logic bundle_ready;
  logic drain_idle;

  a2_batched_iwrr_k2 dut (.*);

  string vectors;
  integer fd, rc, cycle;
  integer vi_rst, vi_ready, vi_req, ex_count, ex_a0, ex_a1, ex_bitmap;
  integer ex_cursor, ex_ptrs, ex_drain_idle;

  task automatic check_equal(input integer observed, expected, input string name);
    if (observed !== expected) begin
      $display("A2_K2_LOCKSTEP_FAIL cycle=%0d field=%s observed=%0h expected=%0h",
               cycle, name, observed, expected);
      $fatal(1);
    end
  endtask

  initial begin
    if (!$value$plusargs("VECTORS=%s", vectors))
      $fatal(1, "missing +VECTORS");
    fd = $fopen(vectors, "r");
    if (fd == 0) $fatal(1, "cannot open vectors");
    cycle = 0;
    while (!$feof(fd)) begin
      rc = $fscanf(fd, "%d %d %x %x %x %x %x %x %x %x\n",
                   vi_rst, vi_ready, vi_req, ex_count, ex_a0, ex_a1,
                   ex_bitmap, ex_cursor, ex_ptrs, ex_drain_idle);
      if (rc == 10) begin
        rst = vi_rst[0];
        bundle_ready = vi_ready[0];
        req = vi_req[15:0];
        #1;
        check_equal(grant_count, ex_count, "count");
        check_equal(grant_addr0, ex_a0, "addr0");
        check_equal(grant_addr1, ex_a1, "addr1");
        check_equal(grant_bitmap, ex_bitmap, "bitmap");
        check_equal(dut.token_cursor_q, ex_cursor, "cursor");
        check_equal({dut.row_ptr_q[3], dut.row_ptr_q[2],
                     dut.row_ptr_q[1], dut.row_ptr_q[0]}, ex_ptrs, "pointers");
        check_equal(drain_idle, ex_drain_idle, "drain_idle");
        clk = 1'b1;
        #1;
        clk = 1'b0;
        #1;
        cycle = cycle + 1;
      end
    end
    $fclose(fd);
    $display("A2_K2_VERILATOR_LOCKSTEP_PASS cycles=%0d", cycle);
    $finish;
  end
endmodule
