`timescale 1ns/1ps

module a8_k2_blackbox_tb;
  logic clk = 0;
  always #5 clk = ~clk;

  logic rst;
  logic [15:0] req;
  logic bundle_ready;
  integer mutation_mode;
  logic [1:0] grant_count;
  logic [3:0] grant_addr0, grant_addr1;
  logic [15:0] accept_mask;
  logic drain_idle;
  integer expected [0:15];
  integer index;
  integer timeout;
  integer source4_occurrences;
  logic [15:0] accepted_now;
  logic [1:0] held_count;
  logic [3:0] held_addr0, held_addr1;

  a8_k2_blackbox_adapter dut (.*);

  task automatic fail(input string diagnostic, input string detail);
    begin
      $display("A8_K2_BLACKBOX_FAIL diagnostic=%s detail=%s", diagnostic, detail);
      $fatal(1, "A8 K2 black-box failure");
    end
  endtask

  function automatic logic [15:0] address_mask;
    input logic [1:0] count;
    input logic [3:0] addr0;
    input logic [3:0] addr1;
    begin
      address_mask = 0;
      if (count >= 1) address_mask[addr0] = 1;
      if (count == 2) address_mask[addr1] = 1;
    end
  endfunction

  task automatic wait_offer;
    begin
      #1;
      timeout = 0;
      while (grant_count == 0 && timeout < 20) begin
        @(negedge clk); #1;
        timeout = timeout + 1;
      end
      if (timeout == 20)
        fail("MISSING_OFFER", "timed out waiting for nonempty bundle");
    end
  endtask

  task automatic check_offer(input integer first, input integer second);
    logic [15:0] expected_mask;
    begin
      wait_offer();
      if (grant_count != 2)
        fail("GLOBAL_ORDER", $sformatf("count=%0d expected=2", grant_count));
      if (grant_addr0 == grant_addr1)
        fail("DUPLICATE_SOURCE", $sformatf("addr=%0d", grant_addr0));
      if (grant_addr0 != first || grant_addr1 != second)
        fail("GLOBAL_ORDER", $sformatf("got=%0d,%0d expected=%0d,%0d",
                                      grant_addr0, grant_addr1, first, second));
      expected_mask = address_mask(grant_count, grant_addr0, grant_addr1);
      if (bundle_ready && accept_mask !== expected_mask)
        fail("ACK_BIJECTION", $sformatf("ack=%h expected=%h",
                                       accept_mask, expected_mask));
      if (req != 0 && drain_idle)
        fail("PREMATURE_DRAIN", $sformatf("req=%h", req));
    end
  endtask

  task automatic commit_and_clear;
    begin
      accepted_now = accept_mask;
      @(posedge clk); #1;
      req = req & ~accepted_now;
    end
  endtask

  task automatic reset_clean;
    begin
      @(negedge clk);
      rst = 1;
      #1;
      if (grant_count != 0 || accept_mask != 0)
        fail("RESET_PHANTOM", $sformatf("count=%0d ack=%h", grant_count, accept_mask));
      req = 0;
      @(posedge clk); #1;
      if (grant_count != 0 || accept_mask != 0 || !drain_idle)
        fail("RESET_DRAIN", $sformatf("count=%0d ack=%h drain=%b",
                                     grant_count, accept_mask, drain_idle));
      @(negedge clk); rst = 0;
    end
  endtask

  initial begin
    if (!$value$plusargs("MUTATION=%d", mutation_mode)) mutation_mode = 0;
    rst = 1;
    req = 0;
    bundle_ready = 1;
    source4_occurrences = 0;
`ifdef A8_OWNER_A2
    expected[0]=4; expected[1]=8; expected[2]=0; expected[3]=5;
    expected[4]=9; expected[5]=12; expected[6]=6; expected[7]=10;
    expected[8]=7; expected[9]=11; expected[10]=13; expected[11]=14;
    expected[12]=15; expected[13]=1; expected[14]=2; expected[15]=3;
`else
    expected[0]=4; expected[1]=11; expected[2]=5; expected[3]=10;
    expected[4]=7; expected[5]=1; expected[6]=8; expected[7]=6;
    expected[8]=9; expected[9]=15; expected[10]=0; expected[11]=14;
    expected[12]=3; expected[13]=12; expected[14]=2; expected[15]=13;
`endif
    repeat (2) @(posedge clk);
    #1;
    if (grant_count != 0 || accept_mask != 0 || !drain_idle)
      fail("RESET_DRAIN", "initial reset is not quiet");
    @(negedge clk); rst = 0; req = 16'hffff;

    // Flattened global order is checked across the lane boundary, not merely
    // as an unordered set per cycle. Clear exactly the accepted pending bits.
    for (index = 0; index < 16; index = index + 2) begin
      check_offer(expected[index], expected[index+1]);
      commit_and_clear();
    end
    repeat (2) @(negedge clk);
    if (req != 0 || grant_count != 0 || !drain_idle)
      fail("FINAL_DRAIN", $sformatf("req=%h count=%0d drain=%b",
                                   req, grant_count, drain_idle));
    $display("A8_K2_FLATTENED_GLOBAL_ORDER_PASS events=16");

    reset_clean();

    // Establish a held atomic bundle, add an unrelated request without
    // withdrawing either offered source, and require stable count/order.
    bundle_ready = 0;
`ifdef A8_OWNER_A2
    req = 16'h0110;
`else
    req = 16'h0810;
`endif
    wait_offer();
    @(posedge clk); #1;
    held_count = grant_count; held_addr0 = grant_addr0; held_addr1 = grant_addr1;
    req = req | 16'h8000;
    repeat (2) begin
      @(negedge clk);
      if (grant_count != held_count || grant_addr0 != held_addr0 ||
          grant_addr1 != held_addr1 || accept_mask != 0)
        fail("ATOMIC_HOLD", "held bundle or acknowledgement changed under stall");
    end
    bundle_ready = 1;
    @(posedge clk); #1;
    $display("A8_K2_ATOMIC_HOLD_PASS");

    reset_clean();

    // A new occurrence of source 4 coexists with a different pending source
    // after the prior source-4 occurrence committed and cleared.
    bundle_ready = 1;
`ifdef A8_OWNER_A2
    req = 16'h0110;
    check_offer(4, 8);
`else
    req = 16'h0810;
    check_offer(4, 11);
`endif
    if (grant_addr0 == 4 || grant_addr1 == 4) source4_occurrences++;
    commit_and_clear();
`ifdef A8_OWNER_A2
    req = 16'h0210;
    check_offer(4, 9);
`else
    req = 16'h0018;
    check_offer(4, 3);
`endif
    if (grant_addr0 == 4 || grant_addr1 == 4) source4_occurrences++;
    commit_and_clear();
    if (source4_occurrences != 2)
      fail("RETRIGGER_COEXISTENCE", $sformatf("source4=%0d", source4_occurrences));
    repeat (2) @(negedge clk);
    if (req != 0 || grant_count != 0 || !drain_idle)
      fail("FINAL_DRAIN", "retrigger cohort did not drain");
    $display("A8_K2_RETRIGGER_COEXISTENCE_PASS source=4 occurrences=2");

    // Reset an actual held offer. The external bitmap is cleared with reset;
    // no old bundle may appear after release.
    bundle_ready = 0;
`ifdef A8_OWNER_A2
    req = 16'h0110;
`else
    req = 16'h0810;
`endif
    wait_offer();
    @(posedge clk); #1;
    reset_clean();
    bundle_ready = 1;
    repeat (3) begin
      @(negedge clk);
      if (grant_count != 0 || accept_mask != 0 || !drain_idle)
        fail("STALE_POST_RESET", "held pre-reset bundle reappeared");
    end
    $display("A8_K2_RESET_DRAIN_STALE_PASS");
    $display("A8_K2_BLACKBOX_PASS mutation=%0d", mutation_mode);
    $finish;
  end
endmodule
