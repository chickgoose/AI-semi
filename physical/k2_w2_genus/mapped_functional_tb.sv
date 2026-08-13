`timescale 1ns/1ps

`ifndef W2_FUNCTIONAL_DUT
  `error "W2_FUNCTIONAL_DUT must name the exact staged or mapped top"
`endif

module k2_w2_mapped_functional_tb;
  logic ref_clk_i = 1'b0;
  logic sample_clk_i = 1'b0;
  logic rst_n = 1'b0;
  logic [15:0] source_pending_i = '0;
  logic [15:0] source_accept_o;
  logic link_clk_o;
`ifdef W2_FUNCTIONAL_R1
  logic [1:0] link_data_o;
`else
  logic [4:0] link_data_o;
`endif
  logic [1:0] retire_valid_o;
  logic [3:0] retire_addr0_o, retire_addr1_o;
  logic drain_idle_o, protocol_error_o;
  integer accepted_by_source [0:15];
  integer accepted_total, retired_total, errors, cycles, fd, source;
  string result_path, sdf_path;

  `W2_FUNCTIONAL_DUT dut (.*);

  always #5 ref_clk_i = ~ref_clk_i;
  initial begin
    #7.5 sample_clk_i = 1'b1;
    forever #5 sample_clk_i = ~sample_clk_i;
  end

  task automatic fail(input string message);
    begin
      $fdisplay(fd, "FAIL %s", message);
      $fatal(1, "W2_MAPPED_FUNCTIONAL_FAIL %s", message);
    end
  endtask

  task automatic offer_and_drain(input logic [15:0] offer);
    integer timeout;
    begin
      @(negedge ref_clk_i);
      source_pending_i = offer;
      timeout = 0;
      while ((source_pending_i != '0) && (timeout < 512)) begin
        @(negedge ref_clk_i);
        timeout = timeout + 1;
      end
      if (timeout >= 512) fail("source accept timeout");
    end
  endtask

  task automatic require_drain();
    integer timeout;
    begin
      timeout = 0;
      while ((!drain_idle_o || (retire_valid_o != '0)) && (timeout < 512)) begin
        @(negedge ref_clk_i);
        timeout = timeout + 1;
      end
      if (timeout >= 512) fail("drain timeout");
    end
  endtask

  always @(posedge ref_clk_i) begin
    cycles = cycles + 1;
    if (!rst_n) begin
      if ((source_accept_o !== '0) || (retire_valid_o !== '0) ||
          (protocol_error_o !== 1'b0))
        fail("nonquiet reset");
    end else begin
      if (protocol_error_o) fail("protocol error");
      if ((source_accept_o & ~source_pending_i) != '0)
        fail("accepted nonpending source");
      if (source_accept_o != '0) begin
        $fdisplay(fd, "A %04h", source_accept_o);
        for (source = 0; source < 16; source = source + 1)
          if (source_accept_o[source]) begin
            accepted_by_source[source] = accepted_by_source[source] + 1;
            accepted_total = accepted_total + 1;
          end
        source_pending_i <= source_pending_i & ~source_accept_o;
      end
      if (retire_valid_o != '0) begin
        if ((retire_valid_o != 2'b01) && (retire_valid_o != 2'b11))
          fail("illegal retire valid shape");
        if (accepted_by_source[retire_addr0_o] <= 0)
          fail("phantom lane0 retire");
        accepted_by_source[retire_addr0_o] =
          accepted_by_source[retire_addr0_o] - 1;
        retired_total = retired_total + 1;
        if (retire_valid_o[1]) begin
          if (accepted_by_source[retire_addr1_o] <= 0)
            fail("phantom lane1 retire");
          accepted_by_source[retire_addr1_o] =
            accepted_by_source[retire_addr1_o] - 1;
          retired_total = retired_total + 1;
        end
        $fdisplay(fd, "R %01h %01h %01h", retire_valid_o,
                  retire_addr0_o, retire_addr1_o);
      end
    end
  end

  initial begin
    if (!$value$plusargs("RESULT=%s", result_path))
      $fatal(2, "missing RESULT plusarg");
    fd = $fopen(result_path, "w");
    if (fd == 0) $fatal(2, "cannot open result");
`ifdef W2_FUNCTIONAL_SDF
    $sdf_annotate(`W2_FUNCTIONAL_SDF, dut);
`endif
    accepted_total = 0;
    retired_total = 0;
    errors = 0;
    cycles = 0;
    for (source = 0; source < 16; source = source + 1)
      accepted_by_source[source] = 0;

    repeat (4) @(posedge ref_clk_i);
    @(negedge ref_clk_i); rst_n = 1'b1;
    offer_and_drain(16'h0003);
    offer_and_drain(16'h00f0);
    offer_and_drain(16'hffff);
    offer_and_drain(16'h8421);
    require_drain();

    // A direct second reset after complete drain proves quiet and stale-state
    // behavior at the exact staged/mapped top boundary.
    @(negedge ref_clk_i); rst_n = 1'b0;
    repeat (4) @(posedge ref_clk_i);
    @(negedge ref_clk_i); rst_n = 1'b1;
    repeat (4) @(posedge ref_clk_i);
    offer_and_drain(16'h5a5a);
    offer_and_drain(16'ha5a5);
    require_drain();
    repeat (4) @(posedge ref_clk_i);

    for (source = 0; source < 16; source = source + 1)
      if (accepted_by_source[source] != 0) fail("loss at final drain");
    if (accepted_total != retired_total) fail("conservation mismatch");
    $fdisplay(fd, "TOTAL %0d %0d", accepted_total, retired_total);
    $fdisplay(fd, "PASS");
    $fclose(fd);
    $display("W2_MAPPED_FUNCTIONAL_TB_PASS accepted=%0d retired=%0d",
             accepted_total, retired_total);
    $finish;
  end
endmodule
