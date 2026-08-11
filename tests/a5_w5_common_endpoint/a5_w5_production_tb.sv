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
  integer ddr_data_toggles, ddr_control_toggles, ddr_clock_toggles;
  integer par_data_toggles, par_control_toggles, par_clock_toggles;
  bit count_enable;
  logic [17:0] ddr_data_prev;
  logic [15:0] par_data_prev;
  logic [8:0] ddr_control_prev, par_control_prev;
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

  function automatic integer pop9(input logic [8:0] value);
    integer i; begin pop9=0; for (i=0;i<9;i=i+1) pop9=pop9+value[i]; end
  endfunction

  wire [17:0] ddr_data_now = {event_addr_i, dut_ddr.tx.event_addr_q, ddr_data,
                              dut_ddr.raw_retire_addr, ddr_retire_addr};
  wire [15:0] par_data_now = {event_addr_i, par_data, dut_parallel.raw_retire_addr,
                              par_retire_addr};
  wire [8:0] ddr_control_now = {event_valid_i, ddr_ready, dut_ddr.launch_fire,
    ddr_retire_valid, ddr_idle, dut_ddr.tx.frame_active_o,
    dut_ddr.raw_retire_toggle, dut_ddr.seen_retire_toggle,
    dut_ddr.launch_qualifier.reset_release_armed_q};
  wire [8:0] par_control_now = {event_valid_i, par_ready, dut_parallel.launch_fire,
    par_retire_valid, par_idle, dut_parallel.frame_active_q,
    dut_parallel.raw_retire_toggle, dut_parallel.seen_retire_toggle,
    dut_parallel.launch_qualifier.reset_release_armed_q};

  always @(ddr_data_now) if (count_enable) begin
    ddr_data_toggles = ddr_data_toggles + $countones(ddr_data_now ^ ddr_data_prev);
    ddr_data_prev = ddr_data_now;
  end
  always @(par_data_now) if (count_enable) begin
    par_data_toggles = par_data_toggles + $countones(par_data_now ^ par_data_prev);
    par_data_prev = par_data_now;
  end
  always @(ddr_control_now) if (count_enable) begin
    ddr_control_toggles = ddr_control_toggles + pop9(ddr_control_now ^ ddr_control_prev);
    ddr_control_prev = ddr_control_now;
  end
  always @(par_control_now) if (count_enable) begin
    par_control_toggles = par_control_toggles + pop9(par_control_now ^ par_control_prev);
    par_control_prev = par_control_now;
  end
  always @(ref_clk_i or sample_clk_i) if (count_enable) begin
    ddr_clock_toggles = ddr_clock_toggles + 1;
    par_clock_toggles = par_clock_toggles + 1;
  end
  always @(ddr_clk) if (count_enable) ddr_clock_toggles = ddr_clock_toggles + 1;
  always @(par_clk) if (count_enable) par_clock_toggles = par_clock_toggles + 1;

  always @(posedge ref_clk_i) begin
    // A real registered consumer samples the producer's pre-NBA outputs here.
    // Deliberately do not delay this observation past the producer always_ff.
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
    if (rst_n && event_valid_i) begin
      if (!ddr_ready || !par_ready) begin errors=errors+1; $error("ready mismatch"); end
      else begin
        $display("ACCEPT D %0d %0d %0d", observer_id[accepted], address[accepted], cycle*4);
        $display("ACCEPT P %0d %0d %0d", observer_id[accepted], address[accepted], cycle*4);
        accepted = accepted + 1;
      end
    end
    if (rst_n && ddr_retire_valid) begin
      $display("RETIRE D %0d %0d %0d", observer_id[ddr_retired], ddr_retire_addr, cycle*4);
      if (ddr_retire_addr !== address[ddr_retired][3:0]) errors=errors+1;
      ddr_retired=ddr_retired+1;
    end
    if (rst_n && par_retire_valid) begin
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
    ddr_data_toggles=0; ddr_control_toggles=0; ddr_clock_toggles=0;
    par_data_toggles=0; par_control_toggles=0; par_clock_toggles=0;
    count_enable=0;
    repeat(2) @(negedge sample_clk_i);
    rst_n=1;
    @(posedge ref_clk_i); #1ps; // charged reset-release arming edge
    @(negedge ref_clk_i);
    cycle=0; count_enable=1;
    ddr_data_prev=ddr_data_now; par_data_prev=par_data_now;
    ddr_control_prev=ddr_control_now; par_control_prev=par_control_now;
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
    count_enable=0;
    if (accepted!=count || ddr_retired!=count || par_retired!=count || errors!=0)
      $fatal(1,"accounting failed count=%0d accepted=%0d ddr=%0d par=%0d errors=%0d",
             count,accepted,ddr_retired,par_retired,errors);
    $display("SUMMARY D %0d %0d %0d",ddr_data_toggles,ddr_control_toggles,ddr_clock_toggles);
    $display("SUMMARY P %0d %0d %0d",par_data_toggles,par_control_toggles,par_clock_toggles);
    $display("RESET 2 0 0 1");
    $finish;
  end
endmodule
