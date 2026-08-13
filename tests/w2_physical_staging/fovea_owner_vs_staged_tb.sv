`timescale 1ns/1ps

module w2_fovea_owner_vs_staged_tb;
  logic ref_clk = 1'b0;
  logic sample_clk = 1'b0;
  logic rst_n = 1'b0;
  logic [15:0] pending = '0;

  logic [15:0] owner_accept, staged_accept;
  logic owner_clk, staged_clk;
  logic [1:0] owner_data, staged_data;
  logic owner_retire_valid, owner_drain, owner_error;
  logic [3:0] owner_retire_addr;
  logic [1:0] staged_retire_valid;
  logic [3:0] staged_retire_addr0, staged_retire_addr1;
  logic staged_drain, staged_error;
  logic [15:0] accepted_edge = '0;
  integer checks = 0;
  integer accepts = 0;
  integer retires = 0;
  integer epoch_accept_base = 0, epoch_retire_base = 0;

  always #8 ref_clk = ~ref_clk;
  initial begin #4; forever #8 sample_clk = ~sample_clk; end

  a7_weighted_fovea_ddr owner (
    .ref_clk_i(ref_clk), .sample_clk_i(sample_clk), .rst_n,
    .source_valid(pending), .source_ready(owner_accept),
    .burst_clk_o(owner_clk), .burst_data_o(owner_data),
    .retire_addr_o(owner_retire_addr), .retire_valid_o(owner_retire_valid),
    .drain_idle_o(owner_drain), .protocol_fault_o(owner_error)
  );
  w2_fovea_r1_physical_staging_top staged (
    .ref_clk_i(ref_clk), .sample_clk_i(sample_clk), .rst_n,
    .source_pending_i(pending), .source_accept_o(staged_accept),
    .link_clk_o(staged_clk), .link_data_o(staged_data),
    .retire_valid_o(staged_retire_valid),
    .retire_addr0_o(staged_retire_addr0),
    .retire_addr1_o(staged_retire_addr1), .drain_idle_o(staged_drain),
    .protocol_error_o(staged_error)
  );

  task automatic compare_all(input string phase);
    begin
      checks = checks + 1;
      if (owner_accept !== staged_accept || owner_clk !== staged_clk ||
          owner_data !== staged_data ||
          {1'b0, owner_retire_valid} !== staged_retire_valid ||
          owner_retire_addr !== staged_retire_addr0 ||
          staged_retire_addr1 !== 4'd0 || owner_drain !== staged_drain ||
          owner_error !== staged_error)
        $fatal(1, "W2_FOVEA_STAGING_LOCKSTEP_FAIL phase=%s check=%0d", phase, checks);
      if (rst_n && ((staged_accept & ~pending) != 16'd0 ||
                    !$onehot0(staged_accept)))
        $fatal(1, "W2_FOVEA_STAGING_ACCEPT_FAIL");
    end
  endtask

  always @(posedge ref_clk) begin
    accepted_edge = staged_accept;
    if (rst_n && |staged_accept) accepts = accepts + 1;
    if (rst_n && staged_error !== 1'b0)
      $fatal(1, "W2_FOVEA_STAGING_PROTOCOL_ERROR t=%0t pending=%h accept=%h ov=%b oa=%h sv=%b sa=%h", $time, pending, staged_accept, owner.fovea_valid, owner.fovea_addr, staged.fovea_valid, staged.fovea_addr);
    #1 compare_all("ref-rise");
    if (owner_retire_valid) retires = retires + 1;
  end
  always @(negedge ref_clk) begin
    #2;
  end
  always @(negedge ref_clk) #1 compare_all("ref-fall");
  always @(posedge sample_clk) #1 compare_all("sample-rise");
  always @(negedge sample_clk) #1 compare_all("sample-fall");

  task automatic low_phase_reset;
    begin
      while (!owner_drain || !staged_drain) @(posedge ref_clk);
      @(negedge sample_clk);
      if (sample_clk !== 1'b0) $fatal(1, "W2_STAGING_RESET_PHASE_FAIL");
      rst_n = 1'b0;
      repeat (2) @(posedge ref_clk);
      @(negedge sample_clk);
      if (sample_clk !== 1'b0) $fatal(1, "W2_STAGING_RESET_PHASE_FAIL");
      rst_n = 1'b1;
      repeat (2) @(posedge ref_clk);
    end
  endtask

  task automatic drain_sources;
    integer timeout;
    begin
      timeout = 0;
      while (|pending) begin
        @(negedge ref_clk);
        #3;
        pending = pending & ~accepted_edge;
        timeout = timeout + 1;
        if (timeout > 512) $fatal(1, "W2_FOVEA_DRAIN_PENDING_TIMEOUT");
      end
      while (!owner_drain || !staged_drain) begin
        @(posedge ref_clk);
        timeout = timeout + 1;
        if (timeout > 768) $fatal(1, "W2_FOVEA_DRAIN_IDLE_TIMEOUT");
      end
      if ((accepts-epoch_accept_base) != (retires-epoch_retire_base) ||
          owner_error !== 1'b0 || staged_error !== 1'b0)
        $fatal(1,"W2_FOVEA_EPOCH_CONSERVATION accept=%0d retire=%0d",
          accepts-epoch_accept_base,retires-epoch_retire_base);
      epoch_accept_base=accepts; epoch_retire_base=retires;
    end
  endtask

  initial begin
    repeat (3) @(posedge ref_clk);
    @(negedge sample_clk); rst_n = 1'b1;
    repeat (2) @(posedge ref_clk);
    @(negedge ref_clk);
    #3;

    // Full contention exercises the canonical 1:5:5:1 weighted row owner.
    pending = 16'hffff;
    repeat (96) begin
      @(negedge ref_clk);
      #3;
      pending = (pending & ~accepted_edge) | (16'b1 << (($time/16) % 16));
    end
    drain_sources();
    @(negedge ref_clk);

    // Gaps, singleton traversal, and same-address retrigger.
    for (integer i = 0; i < 64; i = i + 1) begin
      drain_sources();
      @(negedge ref_clk);
      #3;
      if ((i % 5) != 0) pending = 16'b1 << (i % 16);
    end
    drain_sources();
    @(negedge ref_clk);
    low_phase_reset();
    @(negedge ref_clk);
    #3;
    repeat (12) begin
      pending = 16'h0008;
      drain_sources();
      @(negedge ref_clk);
      #3;
    end

    if (accepts < 80 || accepts != retires)
      $fatal(1, "W2_FOVEA_STAGING_COVERAGE_FAIL accept=%0d retire=%0d", accepts, retires);
    $display("W2_FOVEA_STAGING_LOCKSTEP_PASS checks=%0d accept=%0d retire=%0d",
             checks, accepts, retires);
    $finish;
  end
endmodule
