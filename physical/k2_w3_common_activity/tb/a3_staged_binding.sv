`timescale 1ns/1ps

module aer_legacy_candidate_adapter #(
  parameter int NUM_SOURCES=16, ADDR_WIDTH=16, RETIRE_LANES=2,
  parameter int FIFO_DEPTH=0,
  parameter int SOURCE_WIDTH=(NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if.candidate bench);
  logic sample_clk_i=1'b0; logic [15:0] source_accept;
  logic link_clk; logic [4:0] link_data; logic [1:0] retire_valid;
  logic [3:0] retire_addr0,retire_addr1; logic drain_idle,protocol_error;
  string activity_vcd,activity_window; integer window_fd;
  time window_start,window_end;
  initial begin #7.5 sample_clk_i=1'b1; forever #5 sample_clk_i=~sample_clk_i; end
  w2_a3_p6_physical_staging_top dut (
    .ref_clk_i(bench.clk),.sample_clk_i,.rst_n(bench.rst_n),
    .source_pending_i(bench.source_valid),.source_accept_o(source_accept),
    .link_clk_o(link_clk),.link_data_o(link_data),.retire_valid_o(retire_valid),
    .retire_addr0_o(retire_addr0),.retire_addr1_o(retire_addr1),
    .drain_idle_o(drain_idle),.protocol_error_o(protocol_error));
  always_comb begin
    bench.source_ready='0; bench.retire_valid='0;
    bench.retire_event[0]='0; bench.retire_event[1]='0;
    bench.retire_source[0]='0; bench.retire_source[1]='0;
    if(bench.rst_n===1'b1) begin
      bench.source_ready=source_accept; bench.retire_valid=retire_valid;
      bench.retire_event[0]=ADDR_WIDTH'(retire_addr0);
      bench.retire_event[1]=ADDR_WIDTH'(retire_addr1);
      bench.retire_source[0]=SOURCE_WIDTH'(retire_addr0);
      bench.retire_source[1]=SOURCE_WIDTH'(retire_addr1);
    end
  end
  initial begin
    if(NUM_SOURCES!=16 || ADDR_WIDTH!=16 || RETIRE_LANES!=2 || FIFO_DEPTH!=0)
      $fatal(1,"staged common activity requires N16/A16/K2/FIFO0");
    if($value$plusargs("ACTIVITY_VCD=%s",activity_vcd)) begin
      if(!$value$plusargs("ACTIVITY_WINDOW=%s",activity_window))
        $fatal(1,"ACTIVITY_WINDOW required with ACTIVITY_VCD");
      $dumpfile(activity_vcd); wait(aer_clean_tb.measurement_active===1'b1);
      window_start=$time; $dumpvars(0,dut); $dumpon;
      wait(aer_clean_tb.measurement_active===1'b0);
      window_end=$time; $dumpoff; $dumpflush;
      window_fd=$fopen(activity_window,"w");
      if(window_fd==0)$fatal(1,"cannot create activity window artifact");
      $fwrite(window_fd,"candidate=a3_p6_staged\nstart_tick_1ps=%0t\nend_tick_1ps=%0t\nref_period_ps=10000\nsample_period_ps=10000\nsample_first_rise_ps=7500\nscope=aer_clean_tb.candidate.dut\n",window_start,window_end);
      $fclose(window_fd);
    end
  end
  always @(posedge bench.clk) if(bench.rst_n===1'b1) begin
    for(integer s=0;s<16;s=s+1)
      if(bench.source_valid[s] && bench.source_event[s]!==ADDR_WIDTH'(s))
        $fatal(1,"address-only common activity violation");
    if(protocol_error)$fatal(1,"staged A3+P6 protocol error");
    if((retire_valid & ~bench.retire_ready)!=0)
      $fatal(1,"staged endpoint is always-ready only");
  end
endmodule
