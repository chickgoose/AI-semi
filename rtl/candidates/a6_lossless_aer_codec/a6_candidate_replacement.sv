`timescale 1ns/1ps
// Candidate-only replacement for the cell name instantiated by the frozen TB.
// This file is selected only by the A6 filelist; it does not modify or wrap the
// common scoreboard, source model, trace loader, or measurements.
module aer_legacy_candidate_adapter #(
  parameter int NUM_SOURCES = 16,
  parameter int ADDR_WIDTH = 6,
  parameter int RETIRE_LANES = 1,
  parameter int FIFO_DEPTH = 4,
  parameter int SOURCE_WIDTH = (NUM_SOURCES <= 1) ? 1 : $clog2(NUM_SOURCES)
) (aer_bench_if.candidate bench);
  logic retire_valid;
  logic [ADDR_WIDTH-1:0] retire_event;
  logic [SOURCE_WIDTH-1:0] retire_source;
  logic [1:0] link_count_observe;
  logic [1:0] link_data_observe;
  logic link_ready_observe;
  logic [1:0] previous_link_count;
  logic [1:0] previous_link_data;
  logic previous_link_ready;
  string link_metrics_path;
  integer link_metrics_fd;
  integer observed_link_bits;
  integer observed_link_cycles;
  integer observed_data_toggles;
  integer observed_control_toggles;
  integer lane;

  initial begin
    if (RETIRE_LANES != 1)
      $error("A6 codec frozen candidate has exactly one retire lane");
    link_metrics_fd = 0;
    observed_link_bits = 0;
    observed_link_cycles = 0;
    observed_data_toggles = 0;
    observed_control_toggles = 0;
    previous_link_count = '0;
    previous_link_data = '0;
    previous_link_ready = 1'b0;
    if ($value$plusargs("A6_LINK_METRICS=%s", link_metrics_path)) begin
      link_metrics_fd = $fopen(link_metrics_path, "w");
      if (link_metrics_fd == 0)
        $fatal(1, "cannot open A6 link metrics %s", link_metrics_path);
    end
  end

  a6_lossless_aer_codec_top #(
    .NUM_SOURCES(NUM_SOURCES),
    .EVENT_WIDTH(ADDR_WIDTH)
  ) dut (
    .clk(bench.clk),
    .rst_n(bench.rst_n),
    .source_valid(bench.source_valid),
    .source_ready(bench.source_ready),
    .retire_valid(retire_valid),
    .retire_ready(bench.retire_ready[0]),
    .retire_event(retire_event),
    .retire_source(retire_source),
    .link_count_observe(link_count_observe),
    .link_data_observe(link_data_observe),
    .link_ready_observe(link_ready_observe)
  );

  always_comb begin
    bench.retire_valid = '0;
    for (lane = 0; lane < RETIRE_LANES; lane = lane + 1) begin
      bench.retire_event[lane] = '0;
      bench.retire_source[lane] = '0;
    end
    bench.retire_valid[0] = retire_valid;
    bench.retire_event[0] = retire_event;
    bench.retire_source[0] = retire_source;
  end

  // Candidate-specific passive observation only.  This state is excluded from
  // the synthesizable top/PPA filelist and cannot affect ready, data, or retire.
  always_ff @(posedge bench.clk or negedge bench.rst_n) begin
    if (!bench.rst_n) begin
      previous_link_count <= '0;
      previous_link_data <= '0;
      previous_link_ready <= 1'b0;
    end else begin
      observed_data_toggles <= observed_data_toggles +
        $countones(previous_link_data ^ link_data_observe);
      observed_control_toggles <= observed_control_toggles +
        $countones(previous_link_count ^ link_count_observe) +
        (previous_link_ready ^ link_ready_observe);
      previous_link_count <= link_count_observe;
      previous_link_data <= link_data_observe;
      previous_link_ready <= link_ready_observe;
      if ((link_count_observe != 0) && link_ready_observe) begin
        observed_link_bits <= observed_link_bits + link_count_observe;
        observed_link_cycles <= observed_link_cycles + 1;
        if (link_metrics_fd != 0) begin
          $fwrite(link_metrics_fd, "%0d", link_data_observe[1]);
          if (link_count_observe == 2)
            $fwrite(link_metrics_fd, "%0d", link_data_observe[0]);
        end
      end
    end
  end

  final begin
    if (link_metrics_fd != 0) begin
      $fwrite(link_metrics_fd,
        "\n# bits=%0d cycles=%0d data_toggles=%0d control_toggles=%0d\n",
        observed_link_bits, observed_link_cycles, observed_data_toggles,
        observed_control_toggles);
      $fclose(link_metrics_fd);
    end
  end
endmodule
