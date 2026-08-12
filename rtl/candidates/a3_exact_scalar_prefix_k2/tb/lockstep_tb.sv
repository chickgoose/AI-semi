`timescale 1ns/1ps

module a3_exact_scalar_prefix_k2_lockstep_tb;
  logic clk = 1'b0;
  logic rst;
  logic [15:0] source_pending;
  logic bundle_ready;
  wire [1:0] grant_count;
  wire [3:0] lane0_addr;
  wire [3:0] lane1_addr;

  integer fd;
  integer vector_count;
  integer scan_count;
  integer cycle;
  integer rst_i;
  integer ready_i;
  reg [15:0] req_i;
  integer count_i;
  integer a0_i;
  integer a1_i;
  integer round_i;
  integer center_i;
  integer periph_i;
  integer column_i;
  string vector_path;

  always #5 clk = ~clk;

  a3_exact_scalar_prefix_k2 dut (
    .clk(clk),
    .rst(rst),
    .source_pending(source_pending),
    .grant_count(grant_count),
    .lane0_addr(lane0_addr),
    .lane1_addr(lane1_addr),
    .bundle_ready(bundle_ready)
  );

  task automatic fail_mismatch(input string field_name,
                               input integer expected,
                               input integer actual);
    begin
      $fatal(1,
        "LOCKSTEP_MISMATCH cycle=%0d field=%s expected=%0d actual=%0d req=%h ready=%0d",
        cycle, field_name, expected, actual, source_pending, bundle_ready);
    end
  endtask

  initial begin
    if (!$value$plusargs("VECTORS=%s", vector_path))
      $fatal(1, "LOCKSTEP_MISSING_VECTORS");
    fd = $fopen(vector_path, "r");
    if (fd == 0)
      $fatal(1, "LOCKSTEP_CANNOT_OPEN %s", vector_path);
    scan_count = $fscanf(fd, "%d\n", vector_count);
    if (scan_count != 1 || vector_count < 1)
      $fatal(1, "LOCKSTEP_BAD_HEADER");

    rst = 1'b1;
    source_pending = 16'b0;
    bundle_ready = 1'b0;

    for (cycle = 0; cycle < vector_count; cycle = cycle + 1) begin
      scan_count = $fscanf(fd, "%d %d %h %d %d %d %d %d %d %d\n",
                          rst_i, ready_i, req_i, count_i, a0_i, a1_i,
                          round_i, center_i, periph_i, column_i);
      if (scan_count != 10)
        $fatal(1, "LOCKSTEP_BAD_VECTOR cycle=%0d fields=%0d", cycle, scan_count);
      @(negedge clk);
      rst = rst_i[0];
      bundle_ready = ready_i[0];
      source_pending = req_i;
      @(posedge clk);
      #1;
      if (grant_count !== count_i[1:0]) fail_mismatch("grant_count", count_i, grant_count);
      if (lane0_addr !== a0_i[3:0]) fail_mismatch("lane0_addr", a0_i, lane0_addr);
      if (lane1_addr !== a1_i[3:0]) fail_mismatch("lane1_addr", a1_i, lane1_addr);
      if (dut.round_state !== round_i[2:0]) fail_mismatch("round_state", round_i, dut.round_state);
      if (dut.center_state !== center_i[2:0]) fail_mismatch("center_state", center_i, dut.center_state);
      if (dut.periph_state !== periph_i[2:0]) fail_mismatch("periph_state", periph_i, dut.periph_state);
      if (dut.column_state !== column_i[2:0]) fail_mismatch("column_state", column_i, dut.column_state);
      if (grant_count > 2)
        $fatal(1, "LOCKSTEP_BAD_COUNT cycle=%0d count=%0d", cycle, grant_count);
      if (grant_count == 2 && lane0_addr == lane1_addr)
        $fatal(1, "LOCKSTEP_DUPLICATE cycle=%0d addr=%0d", cycle, lane0_addr);
    end
    $fclose(fd);
    $display("A3_EXACT_SCALAR_PREFIX_K2_LOCKSTEP_PASS vectors=%0d", vector_count);
    $finish;
  end
endmodule
