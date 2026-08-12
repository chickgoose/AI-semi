`timescale 1ns/1ps
/* verilator lint_off DECLFILENAME */

module a4_pcck2_lockstep_tb;
  logic clk;
  logic rst_n;
  logic [15:0] source_valid;
  logic [15:0] source_ready;
  logic [1:0] grant_count;
  logic [7:0] grant_addr;
  logic bundle_ready;
  logic drain_idle;

  a4_paired_cortical_column_k2 dut (.*);
  always #5 clk <= ~clk;

  initial begin
    string vectors_path;
    string case_name;
    integer vectors;
    integer scanned;
    integer cycle;
    logic rst_value;
    logic ready_value;
    logic drain_value;
    logic [15:0] valid_value;
    logic [15:0] expected_source_ready;
    logic [1:0] expected_count;
    logic [3:0] expected_addr0;
    logic [3:0] expected_addr1;
    logic blocked;
    logic [1:0] blocked_count;
    logic [7:0] blocked_addr;

    clk = 1'b0;
    rst_n = 1'b0;
    source_valid = '0;
    bundle_ready = 1'b0;
    blocked = 1'b0;
    blocked_count = '0;
    blocked_addr = '0;
    if (!$value$plusargs("VECTORS=%s", vectors_path))
      $fatal(1, "missing +VECTORS");
    if (!$value$plusargs("CASE=%s", case_name))
      $fatal(1, "missing +CASE");
    vectors = $fopen(vectors_path, "r");
    if (vectors == 0)
      $fatal(1, "cannot open vectors: %s", vectors_path);
    cycle = 0;
    while (!$feof(vectors)) begin
      scanned = $fscanf(vectors, "%d %h %d %h %h %h %h %d\n",
                        rst_value, valid_value, ready_value,
                        expected_source_ready, expected_count,
                        expected_addr0, expected_addr1, drain_value);
      if (scanned == -1) break;
      if (scanned != 8)
        $fatal(1, "malformed vector case=%s cycle=%0d fields=%0d",
               case_name, cycle, scanned);
      @(negedge clk);
      rst_n = rst_value;
      source_valid = valid_value;
      bundle_ready = ready_value;
      #1;
      if ({source_ready, grant_count, grant_addr[7:4], grant_addr[3:0], drain_idle} !==
          {expected_source_ready, expected_count, expected_addr1,
           expected_addr0, drain_value})
        $fatal(1, "lockstep mismatch case=%s cycle=%0d got=%h/%0d/%h/%b exp=%h/%0d/%h%h/%b",
               case_name, cycle, source_ready, grant_count, grant_addr, drain_idle,
               expected_source_ready, expected_count, expected_addr1,
               expected_addr0, drain_value);
      if (rst_n && blocked && !bundle_ready) begin
        if (grant_count != blocked_count)
          $fatal(1, "blocked count changed case=%s cycle=%0d", case_name, cycle);
        if (grant_addr != blocked_addr)
          $fatal(1, "blocked address changed case=%s cycle=%0d", case_name, cycle);
      end
      blocked = rst_n && (grant_count != 0) && !bundle_ready;
      blocked_count = grant_count;
      blocked_addr = grant_addr;
      @(posedge clk);
      #1;
      cycle = cycle + 1;
    end
    $fclose(vectors);
    $display("A4_PCCK2_LOCKSTEP_PASS case=%s cycles=%0d", case_name, cycle);
    $finish;
  end
endmodule
