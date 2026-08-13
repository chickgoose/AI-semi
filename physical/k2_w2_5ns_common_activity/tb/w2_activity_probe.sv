`timescale 1ns/1ps

// TB-only activity probe.  It observes but never drives candidate signals.
module w2_activity_probe #(
  parameter string CANDIDATE_ID = "unset"
) (
  input logic        ref_clk_i,
  input logic        sample_clk_i,
  input logic        rst_n,
  input logic        measurement_active_i,
  input logic        protocol_error_i,
  input logic        drain_idle_i,
  input logic [15:0] source_accept_i,
  input logic [1:0]  retire_valid_i
);
  string raw_vcd_path;
  string window_path;
  integer window_fd;
  integer ref_rises;
  integer sample_rises;
  integer accepts;
  integer retires;
  time window_start;
  time window_end;
  time previous_ref_rise;
  time previous_sample_rise;
  logic seen_ref_rise;
  logic seen_sample_rise;

  always @(posedge ref_clk_i) begin
    if (seen_ref_rise && (($time - previous_ref_rise) != 5ns))
      $fatal(1, "W2_ACTIVITY_REF_PERIOD_NOT_5NS");
    previous_ref_rise = $time;
    seen_ref_rise = 1'b1;
    if (measurement_active_i === 1'b1) begin
      ref_rises = ref_rises + 1;
      for (integer source = 0; source < 16; source = source + 1)
        accepts = accepts + source_accept_i[source];
      retires = retires + retire_valid_i[0] + retire_valid_i[1];
      if (rst_n !== 1'b1 || protocol_error_i !== 1'b0)
        $fatal(1, "W2_ACTIVITY_PROTOCOL_OR_RESET_ERROR");
    end
  end

  always @(posedge sample_clk_i) begin
    if (seen_sample_rise && (($time - previous_sample_rise) != 5ns))
      $fatal(1, "W2_ACTIVITY_SAMPLE_PERIOD_NOT_5NS");
    previous_sample_rise = $time;
    seen_sample_rise = 1'b1;
    if (measurement_active_i === 1'b1)
      sample_rises = sample_rises + 1;
  end

  initial begin
    ref_rises = 0;
    sample_rises = 0;
    accepts = 0;
    retires = 0;
    seen_ref_rise = 1'b0;
    seen_sample_rise = 1'b0;
    if (!$value$plusargs("ACTIVITY_RAW_VCD=%s", raw_vcd_path))
      $fatal(1, "ACTIVITY_RAW_VCD is required");
    if (!$value$plusargs("ACTIVITY_WINDOW=%s", window_path))
      $fatal(1, "ACTIVITY_WINDOW is required");
    $dumpfile(raw_vcd_path);
    wait (measurement_active_i === 1'b1);
    window_start = $time;
    $dumpvars(0, aer_clean_tb.candidate.dut);
    $dumpon;
    wait (measurement_active_i === 1'b0);
    window_end = $time;
    $dumpoff;
    $dumpflush;
    if (window_end <= window_start || !seen_ref_rise || !seen_sample_rise)
      $fatal(1, "W2_ACTIVITY_INVALID_WINDOW");
    if (((previous_sample_rise - previous_ref_rise + 5ns) % 5ns) != 1.25ns)
      $fatal(1, "W2_ACTIVITY_SAMPLE_PHASE_NOT_1P25NS");
    // The producer exclusively creates the output root and rejects this path
    // before launch.  Use the portable IEEE write mode only inside that root.
    window_fd = $fopen(window_path, "w");
    if (window_fd == 0)
      $fatal(1, "W2_ACTIVITY_WINDOW_EXCLUSIVE_CREATE_FAILED");
    $fwrite(window_fd, "schema=w2_5ns_activity_window_v1\n");
    $fwrite(window_fd, "candidate=%s\n", CANDIDATE_ID);
    $fwrite(window_fd, "scope=aer_clean_tb.candidate.dut\n");
    $fwrite(window_fd, "start_tick_1ps=%0t\n", window_start);
    $fwrite(window_fd, "end_tick_1ps=%0t\n", window_end);
    $fwrite(window_fd, "ref_period_ps=5000\n");
    $fwrite(window_fd, "sample_period_ps=5000\n");
    $fwrite(window_fd, "sample_phase_ps=1250\n");
    $fwrite(window_fd, "ref_rises=%0d\n", ref_rises);
    $fwrite(window_fd, "sample_rises=%0d\n", sample_rises);
    $fwrite(window_fd, "accepted_edges=%0d\n", accepts);
    $fwrite(window_fd, "retired_edges=%0d\n", retires);
    $fwrite(window_fd, "drain_idle_at_window_end=%0d\n", drain_idle_i);
    $fclose(window_fd);
    $display("W2_5NS_ACTIVITY_PROBE_PASS candidate=%s start_ps=%0t end_ps=%0t", CANDIDATE_ID, window_start, window_end);
  end
endmodule
