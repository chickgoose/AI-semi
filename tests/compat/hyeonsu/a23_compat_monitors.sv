// Non-intrusive observers. They add protocol evidence without modifying the
// original workloads, scoreboard, assertions, or thresholds.
`ifdef HYEONSU_BIND_AER
module hyeonsu_aer_monitor;
  localparam int NUM_SOURCES = aer_pkg::NUM_SOURCES;
  localparam int ADDR_WIDTH = aer_pkg::ADDR_WIDTH;
  localparam int SRC_WIDTH = aer_pkg::SRC_WIDTH;

  integer protocol_errors = 0;
  integer current_out_stall = 0;
  integer max_out_stall = 0;
  integer phase_accepted = 0;
  integer phase_emitted = 0;
  integer reset_releases = 0;
  integer phase_start_cycle = 0;
  integer accept_cycle [NUM_SOURCES][8];
  integer q_head [NUM_SOURCES];
  integer q_tail [NUM_SOURCES];
  integer q_count [NUM_SOURCES];
  integer phase_latency_sum = 0;
  integer phase_latency_max = 0;
  logic held_valid = 0;
  logic [ADDR_WIDTH-1:0] held_addr;
  logic [SRC_WIDTH-1:0] held_src;
  string wave_path;

  function automatic string phase_name(input integer phase);
`ifdef HYEONSU_LONG_STALL
    if (phase == 1) return "long_stall_backpressure";
`endif
    case (phase)
      1: return "single";
      2: return "simultaneous";
      3: return "burst";
      4: return "backpressure";
      5: return "starvation_probe";
      6: return "all_but_one_saturated";
      7: return "reset_mid_contention_pre_reset";
      8: return "reset_mid_contention_recovery";
      default: return "pre_test";
    endcase
  endfunction

  function automatic integer count_ones(input logic [NUM_SOURCES-1:0] bits);
    integer count;
    count = 0;
    for (integer i = 0; i < NUM_SOURCES; i++) count += bits[i];
    return count;
  endfunction

  task automatic clear_phase;
    phase_accepted = 0;
    phase_emitted = 0;
    phase_latency_sum = 0;
    phase_latency_max = 0;
    phase_start_cycle = aer_tb.cycle_no;
    for (integer i = 0; i < NUM_SOURCES; i++) begin
      q_head[i] = 0;
      q_tail[i] = 0;
      q_count[i] = 0;
    end
  endtask

  task automatic report_phase;
    real avg_latency;
    if (reset_releases > 0) begin
      avg_latency = (phase_emitted == 0) ? 0.0 :
                    real'(phase_latency_sum) / real'(phase_emitted);
      $display("HYEONSU_PHASE_OBSERVER phase=%s accepted=%0d emitted=%0d avg_latency=%0.4f max_latency=%0d cycles=%0d",
               phase_name(reset_releases), phase_accepted, phase_emitted,
               avg_latency, phase_latency_max,
               aer_tb.cycle_no - phase_start_cycle);
    end
  endtask

  initial begin
    clear_phase();
    if ($value$plusargs("HYEONSU_WAVE=%s", wave_path)) begin
      $dumpfile(wave_path);
      $dumpvars(0, aer_tb);
      $display("[A23_COMPAT] waveform=%s", wave_path);
    end
  end

  always @(posedge aer_tb.rst_n) begin
    reset_releases = reset_releases + 1;
    clear_phase();
  end

  always @(negedge aer_tb.rst_n) begin
    report_phase();
  end

  always @(posedge aer_tb.clk) begin
    logic [NUM_SOURCES-1:0] accepted;
    integer src;
    integer latency;
    accepted = aer_tb.in_valid & aer_tb.in_ready;
    if (!aer_tb.rst_n) begin
      current_out_stall = 0;
      held_valid = 0;
    end else begin
      if (count_ones(accepted) > 1) begin
        protocol_errors = protocol_errors + 1;
        $error("A23 compat: multiple input handshakes in one cycle: %b", accepted);
      end
      for (integer i = 0; i < NUM_SOURCES; i++) begin
        if (accepted[i]) begin
          if (q_count[i] >= 8) begin
            protocol_errors = protocol_errors + 1;
            $error("A23 compat observer queue overflow src=%0d", i);
          end else begin
            accept_cycle[i][q_tail[i]] = aer_tb.cycle_no;
            q_tail[i] = (q_tail[i] + 1) % 8;
            q_count[i] = q_count[i] + 1;
          end
          phase_accepted = phase_accepted + 1;
        end
      end

      if (aer_tb.out_valid && aer_tb.out_ready) begin
        src = aer_tb.out_src;
        phase_emitted = phase_emitted + 1;
        if (src < 0 || src >= NUM_SOURCES || q_count[src] == 0) begin
          protocol_errors = protocol_errors + 1;
          $error("A23 compat observer saw output without matching input src=%0d", src);
        end else begin
          latency = aer_tb.cycle_no - accept_cycle[src][q_head[src]];
          q_head[src] = (q_head[src] + 1) % 8;
          q_count[src] = q_count[src] - 1;
          phase_latency_sum = phase_latency_sum + latency;
          if (latency > phase_latency_max) phase_latency_max = latency;
        end
      end

      if (aer_tb.out_valid && !aer_tb.out_ready) begin
        current_out_stall = current_out_stall + 1;
        if (current_out_stall > max_out_stall) max_out_stall = current_out_stall;
        if (held_valid &&
            (aer_tb.out_addr !== held_addr || aer_tb.out_src !== held_src)) begin
          protocol_errors = protocol_errors + 1;
          $error("A23 compat: output payload/source changed under backpressure");
        end
        held_valid = 1;
        held_addr = aer_tb.out_addr;
        held_src = aer_tb.out_src;
      end else begin
        current_out_stall = 0;
        held_valid = 0;
      end
    end
  end

  final begin
    real avg_latency;
    avg_latency = (phase_emitted == 0) ? 0.0 :
                  real'(phase_latency_sum) / real'(phase_emitted);
    if (reset_releases > 0) begin
      $display("HYEONSU_PHASE_OBSERVER phase=%s accepted=%0d emitted=%0d avg_latency=%0.4f max_latency=%0d cycles=%0d",
               phase_name(reset_releases), phase_accepted, phase_emitted,
               avg_latency, phase_latency_max,
               aer_tb.cycle_no - phase_start_cycle);
    end
    $display("HYEONSU_AER_MONITOR errors=%0d max_out_stall=%0d reset_releases=%0d",
             protocol_errors, max_out_stall, reset_releases);
  end
endmodule
`endif

`ifdef HYEONSU_BIND_ARBITER
module hyeonsu_arbiter_monitor;
  localparam int NUM_SOURCES = 256;
  integer monitor_errors = 0;
  integer wait_handshakes [NUM_SOURCES];
  integer max_wait = 0;

  function automatic integer count_ones(input logic [NUM_SOURCES-1:0] bits);
    integer count;
    count = 0;
    for (integer i = 0; i < NUM_SOURCES; i++) count += bits[i];
    return count;
  endfunction

  initial begin
    for (integer i = 0; i < NUM_SOURCES; i++) wait_handshakes[i] = 0;
  end

  always @(posedge dual_level_arbiter_tb.clk) begin
    if (!dual_level_arbiter_tb.rst_n) begin
      for (integer i = 0; i < NUM_SOURCES; i++) wait_handshakes[i] = 0;
    end else begin
      if (count_ones(dual_level_arbiter_tb.grant) > 1) begin
        monitor_errors = monitor_errors + 1;
        $error("A23 arbiter adapter: grant is not onehot0");
      end
      if ((dual_level_arbiter_tb.grant & ~dual_level_arbiter_tb.req) != '0) begin
        monitor_errors = monitor_errors + 1;
        $error("A23 arbiter adapter: grant asserted without request");
      end
      if (dual_level_arbiter_tb.advance) begin
        for (integer i = 0; i < NUM_SOURCES; i++) begin
          if (!dual_level_arbiter_tb.req[i] || dual_level_arbiter_tb.grant[i]) begin
            wait_handshakes[i] = 0;
          end else begin
            wait_handshakes[i] = wait_handshakes[i] + 1;
            if (wait_handshakes[i] > max_wait) max_wait = wait_handshakes[i];
            if (wait_handshakes[i] >= NUM_SOURCES) begin
              monitor_errors = monitor_errors + 1;
              $error("A23 arbiter adapter: source %0d exceeded bounded wait (%0d handshakes)",
                     i, wait_handshakes[i]);
            end
          end
        end
      end
    end
  end

  final begin
    $display("HYEONSU_ARBITER_MONITOR errors=%0d max_wait_handshakes=%0d bound=%0d",
             monitor_errors, max_wait, NUM_SOURCES);
  end
endmodule
`endif
