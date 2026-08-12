`timescale 1ns/1ps

module a3_k2_ordered_link_adapter_lockstep_tb;
  logic clk = 1'b0;
  logic rst;
  logic [1:0] offer_count;
  logic [3:0] offer_addr0;
  logic [3:0] offer_addr1;
  wire offer_ready;
  wire [1:0] retire_valid;
  wire [3:0] retire_addr0;
  wire [3:0] retire_addr1;
  logic [1:0] retire_ready;
  wire link_empty;

  integer fd;
  integer vector_count;
  integer scan_count;
  integer cycle;
  integer rst_i;
  integer offer_count_i;
  integer offer_addr0_i;
  integer offer_addr1_i;
  integer retire_ready_i;
  integer offer_ready_i;
  integer retire_valid_i;
  integer retire_addr0_i;
  integer retire_addr1_i;
  integer link_empty_i;
  integer post_count_i;
  integer post_addr0_i;
  integer post_addr1_i;
  string vector_path;

  always #5 clk = ~clk;

  a3_k2_ordered_link_adapter dut (.*);

  task automatic mismatch(input string field_name,
                          input integer expected,
                          input integer actual);
    begin
      $fatal(1,
        "ADAPTER_LOCKSTEP_MISMATCH cycle=%0d field=%s expected=%0d actual=%0d state=%0d/%0d/%0d offer=%0d/%0d/%0d ready=%0d",
        cycle, field_name, expected, actual,
        dut.count_q, dut.addr0_q, dut.addr1_q,
        offer_count, offer_addr0, offer_addr1, retire_ready);
    end
  endtask

  initial begin
    if (!$value$plusargs("VECTORS=%s", vector_path))
      $fatal(1, "ADAPTER_LOCKSTEP_MISSING_VECTORS");
    fd = $fopen(vector_path, "r");
    if (fd == 0)
      $fatal(1, "ADAPTER_LOCKSTEP_CANNOT_OPEN %s", vector_path);
    scan_count = $fscanf(fd, "%d\n", vector_count);
    if (scan_count != 1 || vector_count < 1)
      $fatal(1, "ADAPTER_LOCKSTEP_BAD_HEADER");

    rst = 1'b1;
    offer_count = 0;
    offer_addr0 = 0;
    offer_addr1 = 0;
    retire_ready = 0;

    for (cycle = 0; cycle < vector_count; cycle = cycle + 1) begin
      scan_count = $fscanf(fd,
        "%d %d %d %d %d %d %d %d %d %d %d %d %d\n",
        rst_i, offer_count_i, offer_addr0_i, offer_addr1_i, retire_ready_i,
        offer_ready_i, retire_valid_i, retire_addr0_i, retire_addr1_i,
        link_empty_i, post_count_i, post_addr0_i, post_addr1_i);
      if (scan_count != 13)
        $fatal(1, "ADAPTER_LOCKSTEP_BAD_VECTOR cycle=%0d fields=%0d",
               cycle, scan_count);

      @(negedge clk);
      rst = rst_i[0];
      offer_count = offer_count_i[1:0];
      offer_addr0 = offer_addr0_i[3:0];
      offer_addr1 = offer_addr1_i[3:0];
      retire_ready = retire_ready_i[1:0];
      #1;
      if (offer_ready !== offer_ready_i[0])
        mismatch("offer_ready", offer_ready_i, offer_ready);
      if (retire_valid !== retire_valid_i[1:0])
        mismatch("retire_valid", retire_valid_i, retire_valid);
      if (retire_addr0 !== retire_addr0_i[3:0])
        mismatch("retire_addr0", retire_addr0_i, retire_addr0);
      if (retire_addr1 !== retire_addr1_i[3:0])
        mismatch("retire_addr1", retire_addr1_i, retire_addr1);
      if (link_empty !== link_empty_i[0])
        mismatch("link_empty", link_empty_i, link_empty);

      @(posedge clk);
      #1;
      if (dut.count_q !== post_count_i[1:0])
        mismatch("post_count", post_count_i, dut.count_q);
      if (dut.addr0_q !== post_addr0_i[3:0])
        mismatch("post_addr0", post_addr0_i, dut.addr0_q);
      if (dut.addr1_q !== post_addr1_i[3:0])
        mismatch("post_addr1", post_addr1_i, dut.addr1_q);
      if (dut.count_q > 2)
        $fatal(1, "ADAPTER_LOCKSTEP_OVERFLOW cycle=%0d count=%0d",
               cycle, dut.count_q);
    end
    $fclose(fd);
    $display("A3_K2_ORDERED_LINK_LOCKSTEP_PASS vectors=%0d", vector_count);
    $finish;
  end
endmodule
