`timescale 1ns/1ps

module w2_a3_owner_vs_staged_tb;
  logic ref_clk = 0, sample_clk = 0, rst_n = 0, link_enable = 1;
  logic [15:0] pending = 0;
  logic owner_valid, owner_ready, owner_commit;
  logic [1:0] owner_count, owner_steps;
  logic [3:0] owner_addr0, owner_addr1;
  logic owner_bundle_error, owner_retire_error;
  logic owner_clk, staged_clk;
  logic [4:0] owner_data, staged_data;
  logic [1:0] owner_rv, staged_rv;
  logic [3:0] owner_ra0, owner_ra1, staged_ra0, staged_ra1;
  logic owner_drain, staged_drain, staged_error;
  logic [15:0] staged_accept;
  logic [15:0] accepted_edge = '0;
  integer checks = 0, accepts = 0, retires = 0, stalls = 0;
  integer epoch_accept_base = 0, epoch_retire_base = 0;

  always #8 ref_clk = ~ref_clk;
  initial begin #4; forever #8 sample_clk = ~sample_clk; end

  a3_exact_scalar_prefix_k2_p6_top owner (
    .ref_clk_i(ref_clk), .sample_clk_i(sample_clk), .rst_n,
    .link_enable_i(link_enable), .source_pending_i(pending),
    .bundle_valid_o(owner_valid), .bundle_ready_o(owner_ready),
    .bundle_commit_o(owner_commit), .grant_count_o(owner_count),
    .grant_addr0_o(owner_addr0), .grant_addr1_o(owner_addr1),
    .policy_microsteps_o(owner_steps),
    .bundle_protocol_error_o(owner_bundle_error), .p6_clk_o(owner_clk),
    .p6_data_o(owner_data), .retire_valid_o(owner_rv),
    .retire_addr0_o(owner_ra0), .retire_addr1_o(owner_ra1),
    .retire_protocol_error_o(owner_retire_error), .drain_idle_o(owner_drain)
  );
  w2_a3_p6_physical_staging_top staged (
    .ref_clk_i(ref_clk), .sample_clk_i(sample_clk), .rst_n,
    .source_pending_i(pending),
    .source_accept_o(staged_accept), .link_clk_o(staged_clk),
    .link_data_o(staged_data), .retire_valid_o(staged_rv),
    .retire_addr0_o(staged_ra0), .retire_addr1_o(staged_ra1),
    .drain_idle_o(staged_drain), .protocol_error_o(staged_error)
  );

  task automatic compare_all(input string phase);
    logic [15:0] expected_accept;
    logic expected_error, expected_drain;
    begin
      expected_accept = 0;
      if (owner_commit) begin
        expected_accept[owner_addr0] = 1;
        if (owner_count == 2) expected_accept[owner_addr1] = 1;
      end
      expected_error = owner_bundle_error || owner_retire_error ||
                       (owner_commit && owner_steps != owner_count);
      expected_drain = rst_n && !(|pending) && owner_drain &&
                       !(|owner_rv) && !expected_error;
      checks = checks + 1;
      if (expected_accept !== staged_accept || owner_clk !== staged_clk ||
          owner_data !== staged_data || owner_rv !== staged_rv ||
          owner_ra0 !== staged_ra0 || owner_ra1 !== staged_ra1 ||
          expected_error !== staged_error || expected_drain !== staged_drain)
        $fatal(1, "W2_A3_STAGING_LOCKSTEP_FAIL phase=%s check=%0d", phase, checks);
      if (rst_n && ((staged_accept & ~pending) != 0 || $countones(staged_accept) > 2))
        $fatal(1, "W2_A3_STAGING_ACCEPT_FAIL");
    end
  endtask
  always @(posedge ref_clk) begin
    accepted_edge = staged_accept;
    if (rst_n && |staged_accept) accepts = accepts + $countones(staged_accept);
    if (rst_n && (owner_bundle_error !== 1'b0 || owner_retire_error !== 1'b0 || staged_error !== 1'b0))
      $fatal(1,"W2_A3_STAGING_PROTOCOL_ERROR");
    #1 compare_all("ref-rise");
    if (rst_n && (owner_bundle_error !== 1'b0 || owner_retire_error !== 1'b0 || staged_error !== 1'b0))
      $fatal(1,"W2_A3_STAGING_PROTOCOL_ERROR_POST_NBA");
    retires = retires + $countones(staged_rv);
    if (owner_valid && !link_enable) stalls = stalls + 1;
  end
  always @(negedge ref_clk) begin
    #2;
  end
  always @(negedge ref_clk) #1 compare_all("ref-fall");
  always @(posedge sample_clk) #1 compare_all("sample-rise");
  always @(negedge sample_clk) #1 compare_all("sample-fall");

  task automatic drain_sources;
    integer timeout;
    begin timeout=0;
      while (|pending) begin
        @(negedge ref_clk); #2; pending = pending & ~accepted_edge;
        timeout++; if(timeout>512)$fatal(1,"W2_A3_DRAIN_PENDING_TIMEOUT");
      end
      while (!owner_drain || !staged_drain) begin
        @(posedge ref_clk); timeout++;
        if(timeout>768)$fatal(1,"W2_A3_DRAIN_IDLE_TIMEOUT");
      end
      if((accepts-epoch_accept_base)!=(retires-epoch_retire_base) ||
         owner_bundle_error!==0 || owner_retire_error!==0 || staged_error!==0)
        $fatal(1,"W2_A3_EPOCH_CONSERVATION");
      epoch_accept_base=accepts;epoch_retire_base=retires;
    end
  endtask

  initial begin
    repeat (3) @(posedge ref_clk);
    @(negedge sample_clk); rst_n = 1; link_enable = 1;
    repeat (2) @(posedge ref_clk);
    @(negedge ref_clk);
    for (integer i = 0; i < 96; i = i + 1) begin
      @(negedge ref_clk);
      pending &= ~accepted_edge;
      if ((i % 4) != 0) pending |= (16'b11 << (i % 14));
      link_enable = 1;
    end
    pending = 16'hffff; link_enable = 1;
    repeat (50) begin @(negedge ref_clk); pending &= ~accepted_edge; end
    drain_sources();
    @(negedge ref_clk);
    @(negedge sample_clk); if (sample_clk !== 0) $fatal(1, "W2_STAGING_RESET_PHASE_FAIL");
    rst_n = 0; repeat (2) @(posedge ref_clk);
    @(negedge sample_clk); rst_n = 1; repeat (2) @(posedge ref_clk);
    @(negedge ref_clk);
    pending = 16'h0101;
    repeat (12) begin @(negedge ref_clk); pending &= ~accepted_edge; end
    drain_sources();
    @(negedge ref_clk);
    if (accepts < 50 || accepts != retires)
      $fatal(1, "W2_A3_STAGING_COVERAGE_FAIL accept=%0d retire=%0d stalls=%0d", accepts, retires, stalls);
    $display("W2_A3_STAGING_LOCKSTEP_PASS checks=%0d accept=%0d retire=%0d stalls=%0d",
             checks, accepts, retires, stalls);
    $finish;
  end
endmodule
