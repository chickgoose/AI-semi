`timescale 1ns/1ps

module a5_w5_production_tb;
  localparam time HALF = 8ns;
  localparam integer MAX_EVENTS = 200000;
  logic ref_clk_i=0, sample_clk_i=0, rst_n=0, event_valid_i=0;
  logic [3:0] event_addr_i=0;
  logic ddr_ready, ddr_clk, ddr_retire_valid, ddr_idle;
  logic [1:0] ddr_data;
  logic [3:0] ddr_retire_addr;
  logic par_ready, par_clk, par_retire_valid, par_idle;
  logic [3:0] par_data, par_retire_addr;
  integer launch [0:MAX_EVENTS-1], occurrence [0:MAX_EVENTS-1];
  integer observer_id [0:MAX_EVENTS-1], address [0:MAX_EVENTS-1];
  integer count, drive_index, accepted, ddr_retired, par_retired, cycle, errors;
  integer shared_data_transitions, shared_control_transitions, shared_clock_transitions;
  integer ddr_data_transitions, ddr_control_transitions, ddr_link_clock_transitions;
  integer par_data_transitions, par_control_transitions, par_link_clock_transitions;
  integer reset_probe_retired, reset_probe_errors;
  bit count_enable, main_traffic;
  logic [13:0] ddr_data_prev;
  logic [11:0] par_data_prev;
  logic [7:0] ddr_control_prev, par_control_prev;
  logic [3:0] shared_data_prev;
  logic shared_control_prev;
  string stim_path;

  a7_r1_candidate_endpoint dut_ddr (
    .ref_clk_i, .sample_clk_i, .rst_n, .event_valid_i, .event_addr_i,
    .event_ready_o(ddr_ready), .burst_clk_o(ddr_clk), .burst_data_o(ddr_data),
    .retire_addr_o(ddr_retire_addr), .retire_valid_o(ddr_retire_valid),
    .drain_idle_o(ddr_idle));
  a7_r1_parallel_reference_top dut_parallel (
    .ref_clk_i, .sample_clk_i, .rst_n, .event_valid_i, .event_addr_i,
    .event_ready_o(par_ready), .link_strobe_o(par_clk), .link_data_o(par_data),
    .retire_addr_o(par_retire_addr), .retire_valid_o(par_retire_valid),
    .drain_idle_o(par_idle));

  initial forever #(HALF) ref_clk_i = ~ref_clk_i;
  initial begin #12ns; sample_clk_i=1; forever #(HALF) sample_clk_i=~sample_clk_i; end

  function automatic integer pop8(input logic [7:0] value);
    integer i; begin pop8=0; for (i=0;i<8;i=i+1) pop8=pop8+value[i]; end
  endfunction

  wire [13:0] ddr_data_now = {dut_ddr.tx.event_addr_q, ddr_data,
                              dut_ddr.raw_retire_addr, ddr_retire_addr};
  wire [11:0] par_data_now = {par_data, dut_parallel.raw_retire_addr,
                              par_retire_addr};
  wire [7:0] ddr_control_now = {ddr_ready, dut_ddr.launch_fire,
    ddr_retire_valid, ddr_idle, dut_ddr.tx.frame_active_o,
    dut_ddr.raw_retire_toggle, dut_ddr.seen_retire_toggle,
    dut_ddr.launch_qualifier.reset_release_armed_q};
  wire [7:0] par_control_now = {par_ready, dut_parallel.launch_fire,
    par_retire_valid, par_idle, dut_parallel.frame_active_q,
    dut_parallel.raw_retire_toggle, dut_parallel.seen_retire_toggle,
    dut_parallel.launch_qualifier.reset_release_armed_q};

  always @(ddr_data_now) if (count_enable) begin
    ddr_data_transitions = ddr_data_transitions + $countones(ddr_data_now ^ ddr_data_prev);
    ddr_data_prev = ddr_data_now;
  end
  always @(par_data_now) if (count_enable) begin
    par_data_transitions = par_data_transitions + $countones(par_data_now ^ par_data_prev);
    par_data_prev = par_data_now;
  end
  always @(ddr_control_now) if (count_enable) begin
    ddr_control_transitions = ddr_control_transitions + pop8(ddr_control_now ^ ddr_control_prev);
    ddr_control_prev = ddr_control_now;
  end
  always @(par_control_now) if (count_enable) begin
    par_control_transitions = par_control_transitions + pop8(par_control_now ^ par_control_prev);
    par_control_prev = par_control_now;
  end
  always @(event_addr_i) if (count_enable) begin
    shared_data_transitions = shared_data_transitions + $countones(event_addr_i ^ shared_data_prev);
    shared_data_prev = event_addr_i;
  end
  always @(event_valid_i) if (count_enable) begin
    shared_control_transitions = shared_control_transitions + (event_valid_i ^ shared_control_prev);
    shared_control_prev = event_valid_i;
  end
  always @(ref_clk_i or sample_clk_i)
    if (count_enable) shared_clock_transitions = shared_clock_transitions + 1;
  always @(ddr_clk) if (count_enable)
    ddr_link_clock_transitions = ddr_link_clock_transitions + 1;
  always @(par_clk) if (count_enable)
    par_link_clock_transitions = par_link_clock_transitions + 1;

  always @(posedge ref_clk_i) begin
    // Sequential-consumer observation samples the producer's pre-NBA outputs.
    // Deliberately do not delay this observation into producer availability.
    if (rst_n && dut_ddr.launch_fire && ddr_idle) begin
      errors=errors+1; $error("DDR drain_idle high during launch_fire");
    end
    if (rst_n && dut_parallel.launch_fire && par_idle) begin
      errors=errors+1; $error("parallel drain_idle high during launch_fire");
    end
    if (rst_n && ddr_retire_valid && ddr_idle) begin
      errors=errors+1; $error("DDR drain_idle high with pending consumer output");
    end
    if (rst_n && par_retire_valid && par_idle) begin
      errors=errors+1; $error("parallel drain_idle high with pending consumer output");
    end
    if (rst_n && main_traffic && event_valid_i) begin
      if (!ddr_ready || !par_ready) begin errors=errors+1; $error("ready mismatch"); end
      else begin
        $display("ACCEPT D %0d %0d %0d", observer_id[accepted], address[accepted], cycle*4);
        $display("ACCEPT P %0d %0d %0d", observer_id[accepted], address[accepted], cycle*4);
        accepted = accepted + 1;
      end
    end
    if (rst_n && main_traffic && ddr_retire_valid) begin
      $display("RETIRE D %0d %0d %0d", observer_id[ddr_retired], ddr_retire_addr, cycle*4);
      if (ddr_retire_addr !== address[ddr_retired][3:0]) errors=errors+1;
      ddr_retired=ddr_retired+1;
    end
    if (rst_n && main_traffic && par_retire_valid) begin
      $display("RETIRE P %0d %0d %0d", observer_id[par_retired], par_retire_addr, cycle*4);
      if (par_retire_addr !== address[par_retired][3:0]) errors=errors+1;
      par_retired=par_retired+1;
    end
    if (rst_n && count_enable) cycle=cycle+1;
  end

  initial begin : main
    integer fd, status, timeout;
    if (!$value$plusargs("STIM=%s", stim_path)) $fatal(1,"missing STIM");
    fd=$fopen(stim_path,"r"); if (!fd) $fatal(1,"cannot open STIM");
    count=0;
    while (!$feof(fd)) begin
      status=$fscanf(fd,"%d %d %d %d\n",launch[count],occurrence[count],
                    observer_id[count],address[count]);
      if (status==4) count=count+1;
    end
    $fclose(fd);
    accepted=0; ddr_retired=0; par_retired=0; cycle=-1; errors=0;
    shared_data_transitions=0; shared_control_transitions=0; shared_clock_transitions=0;
    ddr_data_transitions=0; ddr_control_transitions=0; ddr_link_clock_transitions=0;
    par_data_transitions=0; par_control_transitions=0; par_link_clock_transitions=0;
    count_enable=0; main_traffic=1;
    repeat(2) @(negedge sample_clk_i);
    rst_n=1;
    @(posedge ref_clk_i); #1ps; // charged reset-release arming edge
    @(negedge ref_clk_i);
    cycle=0; count_enable=1;
    ddr_data_prev=ddr_data_now; par_data_prev=par_data_now;
    ddr_control_prev=ddr_control_now; par_control_prev=par_control_now;
    shared_data_prev=event_addr_i; shared_control_prev=event_valid_i;
    for (drive_index=0; drive_index<count; drive_index=drive_index+1) begin
      event_valid_i=0;
      while (cycle < launch[drive_index]) @(negedge ref_clk_i);
      event_valid_i=1; event_addr_i=address[drive_index][3:0];
      @(negedge ref_clk_i);
    end
    event_valid_i=0;
    timeout=0;
    while ((ddr_retired<count || par_retired<count || !ddr_idle || !par_idle) && timeout<32) begin
      @(negedge ref_clk_i); timeout=timeout+1;
    end
    if (accepted!=count || ddr_retired!=count || par_retired!=count || errors!=0)
      $fatal(1,"accounting failed count=%0d accepted=%0d ddr=%0d par=%0d errors=%0d",
             count,accepted,ddr_retired,par_retired,errors);
    count_enable=0; main_traffic=0; event_valid_i=0;

    // Direct second-reset qualification after a complete drain. Reset is held
    // for two cycles, followed by a quiet epoch and one sentinel transaction.
    if (!ddr_idle || !par_idle) $fatal(1,"second reset attempted before drain");
    @(negedge sample_clk_i); rst_n=0;
    repeat (2) begin
      @(posedge ref_clk_i); #1ps;
      if (ddr_retire_valid!==0 || par_retire_valid!==0 ||
          ddr_ready!==0 || par_ready!==0)
        $fatal(1,"non-quiet output during second reset");
    end
    @(negedge sample_clk_i); rst_n=1;
    @(posedge ref_clk_i); #1ps; // re-arm only
    repeat (3) begin
      @(posedge ref_clk_i);
      if (ddr_retire_valid!==0 || par_retire_valid!==0 ||
          ddr_ready!==1 || par_ready!==1)
        $fatal(1,"non-normalized ready/retire in quiet post-reset epoch");
    end
    @(negedge ref_clk_i); event_addr_i=4'ha; event_valid_i=1;
    @(posedge ref_clk_i);
    if (!ddr_ready || !par_ready) $fatal(1,"post-reset sentinel not accepted");
    @(negedge ref_clk_i); event_valid_i=0;
    reset_probe_retired=0; reset_probe_errors=0;
    repeat (8) begin
      @(posedge ref_clk_i);
      if (ddr_retire_valid || par_retire_valid) begin
        if (!(ddr_retire_valid && par_retire_valid) ||
            ddr_retire_addr!==4'ha || par_retire_addr!==4'ha)
          reset_probe_errors=reset_probe_errors+1;
        reset_probe_retired=reset_probe_retired+1;
      end
    end
    if (reset_probe_retired!=1 || reset_probe_errors!=0 || !ddr_idle || !par_idle)
      $fatal(1,"post-reset stale/duplicate/missing sentinel");
    $display("SHARED %0d %0d %0d",shared_data_transitions,
             shared_control_transitions,shared_clock_transitions);
    $display("ENDPOINT D %0d %0d %0d",ddr_data_transitions,
             ddr_control_transitions,ddr_link_clock_transitions);
    $display("ENDPOINT P %0d %0d %0d",par_data_transitions,
             par_control_transitions,par_link_clock_transitions);
    $display("RESET_PROBE 2 3 0 0 1 1 1 1");
    $finish;
  end
endmodule
