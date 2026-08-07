`timescale 1ns/1ps

// Local activity proxy.  All six schedulers see the same deterministic offered
// occurrences and use the same one-entry/source input contract and registered
// single-lane output boundary as the clean-slate candidate.
module a8_toggle_proxy_tb #(
  parameter int NUM_SOURCES = 16,
  parameter int CYCLES = 4096,
  parameter int ADDR_WIDTH = 16,
  parameter int SOURCE_WIDTH = $clog2(NUM_SOURCES),
  parameter int AGE_WIDTH = $clog2(2 * NUM_SOURCES)
);
  logic clk = 1'b0;
  logic rst_n = 1'b0;
  logic [NUM_SOURCES-1:0] pending [6];
  logic [NUM_SOURCES-1:0] ready [6];
  logic [ADDR_WIDTH-1:0] source_event [NUM_SOURCES];
  logic retire_valid [6];
  logic [ADDR_WIDTH-1:0] retire_event [6];
  logic [SOURCE_WIDTH-1:0] retire_source [6];

  longint unsigned toggles [6];
  longint unsigned accepted [6];
  longint unsigned overrun [6];
  integer cycle_index;
  integer source_index;
  integer arch_index;
  integer arrival_a;
  integer arrival_b;

  logic [NUM_SOURCES-1:0] prev_tracked [5];
  logic [AGE_WIDTH-1:0] prev_age [NUM_SOURCES];
  logic [SOURCE_WIDTH-1:0] prev_tie [6];
  logic [SOURCE_WIDTH-1:0] prev_retire_source [6];
  logic prev_retire_valid [6];
  logic [SOURCE_WIDTH-1:0] prev_rr_start;

  logic [0:0] prev_b1_tag [NUM_SOURCES];
  logic [1:0] prev_b2_tag [NUM_SOURCES];
  logic [2:0] prev_b4_tag [NUM_SOURCES];
  logic [3:0] prev_b8_tag [NUM_SOURCES];
  logic [0:0] prev_b1_epoch;
  logic [1:0] prev_b2_epoch;
  logic [2:0] prev_b4_epoch;
  logic [3:0] prev_b8_epoch;
  logic prev_b1_phase;
  logic prev_b2_phase;
  logic [1:0] prev_b4_phase;
  logic [2:0] prev_b8_phase;

  always #5 clk = ~clk;

  a8_rr_reference #(.NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH)) rr (
    .clk, .rst_n, .source_valid(pending[0]), .source_event,
    .source_ready(ready[0]), .retire_valid(retire_valid[0]),
    .retire_event(retire_event[0]), .retire_source(retire_source[0])
  );
  a8_exact_age_reference #(.NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH)) exact (
    .clk, .rst_n, .source_valid(pending[1]), .source_event,
    .source_ready(ready[1]), .retire_valid(retire_valid[1]),
    .retire_event(retire_event[1]), .retire_source(retire_source[1])
  );
  a8_age_calendar_wheel #(.NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH),
    .BUCKET_CYCLES(1), .EPOCH_COUNT(2 * NUM_SOURCES)) b1 (
    .clk, .rst_n, .source_valid(pending[2]), .source_event,
    .source_ready(ready[2]), .retire_valid(retire_valid[2]),
    .retire_event(retire_event[2]), .retire_source(retire_source[2])
  );
  a8_age_calendar_wheel #(.NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH),
    .BUCKET_CYCLES(2), .EPOCH_COUNT(NUM_SOURCES)) b2 (
    .clk, .rst_n, .source_valid(pending[3]), .source_event,
    .source_ready(ready[3]), .retire_valid(retire_valid[3]),
    .retire_event(retire_event[3]), .retire_source(retire_source[3])
  );
  a8_age_calendar_wheel #(.NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH),
    .BUCKET_CYCLES(4), .EPOCH_COUNT(NUM_SOURCES / 2)) b4 (
    .clk, .rst_n, .source_valid(pending[4]), .source_event,
    .source_ready(ready[4]), .retire_valid(retire_valid[4]),
    .retire_event(retire_event[4]), .retire_source(retire_source[4])
  );
  a8_age_calendar_wheel #(.NUM_SOURCES(NUM_SOURCES), .ADDR_WIDTH(ADDR_WIDTH),
    .BUCKET_CYCLES(8), .EPOCH_COUNT(NUM_SOURCES / 4)) b8 (
    .clk, .rst_n, .source_valid(pending[5]), .source_event,
    .source_ready(ready[5]), .retire_valid(retire_valid[5]),
    .retire_event(retire_event[5]), .retire_source(retire_source[5])
  );

  task automatic count_common(input integer a);
    begin
      toggles[a] += $countones(retire_source[a] ^ prev_retire_source[a]);
      toggles[a] += (retire_valid[a] != prev_retire_valid[a]);
      prev_retire_source[a] = retire_source[a];
      prev_retire_valid[a] = retire_valid[a];
      accepted[a] += $countones(ready[a]);
    end
  endtask

  initial begin
    for (source_index = 0; source_index < NUM_SOURCES; source_index++)
      source_event[source_index] = '0;
    for (arch_index = 0; arch_index < 6; arch_index++) begin
      pending[arch_index] = '0;
      toggles[arch_index] = 0;
      accepted[arch_index] = 0;
      overrun[arch_index] = 0;
      prev_tracked[arch_index < 5 ? arch_index : 4] = '0;
      prev_tie[arch_index] = '0;
      prev_retire_source[arch_index] = '0;
      prev_retire_valid[arch_index] = 1'b0;
    end
    prev_rr_start = '0;
    prev_b1_epoch = '0; prev_b2_epoch = '0;
    prev_b4_epoch = '0; prev_b8_epoch = '0;
    prev_b1_phase = '0; prev_b2_phase = '0;
    prev_b4_phase = '0; prev_b8_phase = '0;
    for (source_index = 0; source_index < NUM_SOURCES; source_index++) begin
      prev_age[source_index] = '0;
      prev_b1_tag[source_index] = '0;
      prev_b2_tag[source_index] = '0;
      prev_b4_tag[source_index] = '0;
      prev_b8_tag[source_index] = '0;
    end

    repeat (3) @(posedge clk);
    rst_n = 1'b1;
    for (cycle_index = 0; cycle_index < CYCLES; cycle_index++) begin
      @(negedge clk);
      // 1.25 offered events/cycle: one permuted source every cycle and a
      // second source every fourth cycle.  Each DUT has an independent input
      // latch, so an occupied-source arrival is counted as overrun.
      arrival_a = (cycle_index * 13 + cycle_index / NUM_SOURCES) % NUM_SOURCES;
      arrival_b = (cycle_index * 7 + 3) % NUM_SOURCES;
      for (arch_index = 0; arch_index < 6; arch_index++) begin
        pending[arch_index] = pending[arch_index] & ~ready[arch_index];
        if (pending[arch_index][arrival_a]) overrun[arch_index]++;
        else pending[arch_index][arrival_a] = 1'b1;
        if ((cycle_index % 4) == 0) begin
          if (pending[arch_index][arrival_b]) overrun[arch_index]++;
          else pending[arch_index][arrival_b] = 1'b1;
        end
      end
      @(posedge clk); #1;

      toggles[0] += $countones(rr.scheduler.rr_start ^ prev_rr_start);
      prev_rr_start = rr.scheduler.rr_start;
      count_common(0);

      toggles[1] += $countones(exact.scheduler.tracked ^ prev_tracked[0]);
      toggles[1] += $countones(exact.scheduler.tie_start ^ prev_tie[1]);
      prev_tracked[0] = exact.scheduler.tracked;
      prev_tie[1] = exact.scheduler.tie_start;
      for (source_index = 0; source_index < NUM_SOURCES; source_index++) begin
        toggles[1] += $countones(exact.scheduler.age[source_index] ^ prev_age[source_index]);
        prev_age[source_index] = exact.scheduler.age[source_index];
      end
      count_common(1);

      toggles[2] += $countones(b1.scheduler.tracked ^ prev_tracked[1]);
      toggles[2] += $countones(b1.scheduler.tie_start ^ prev_tie[2]);
      toggles[2] += $countones(b1.scheduler.current_epoch ^ prev_b1_epoch);
      toggles[2] += (b1.scheduler.bucket_phase != prev_b1_phase);
      prev_tracked[1] = b1.scheduler.tracked; prev_tie[2] = b1.scheduler.tie_start;
      prev_b1_epoch = b1.scheduler.current_epoch; prev_b1_phase = b1.scheduler.bucket_phase;
      for (source_index = 0; source_index < NUM_SOURCES; source_index++) begin
        toggles[2] += $countones(b1.scheduler.tag[source_index] ^ prev_b1_tag[source_index]);
        prev_b1_tag[source_index] = b1.scheduler.tag[source_index];
      end
      count_common(2);

      toggles[3] += $countones(b2.scheduler.tracked ^ prev_tracked[2]);
      toggles[3] += $countones(b2.scheduler.tie_start ^ prev_tie[3]);
      toggles[3] += $countones(b2.scheduler.current_epoch ^ prev_b2_epoch);
      toggles[3] += (b2.scheduler.bucket_phase != prev_b2_phase);
      prev_tracked[2] = b2.scheduler.tracked; prev_tie[3] = b2.scheduler.tie_start;
      prev_b2_epoch = b2.scheduler.current_epoch; prev_b2_phase = b2.scheduler.bucket_phase;
      for (source_index = 0; source_index < NUM_SOURCES; source_index++) begin
        toggles[3] += $countones(b2.scheduler.tag[source_index] ^ prev_b2_tag[source_index]);
        prev_b2_tag[source_index] = b2.scheduler.tag[source_index];
      end
      count_common(3);

      toggles[4] += $countones(b4.scheduler.tracked ^ prev_tracked[3]);
      toggles[4] += $countones(b4.scheduler.tie_start ^ prev_tie[4]);
      toggles[4] += $countones(b4.scheduler.current_epoch ^ prev_b4_epoch);
      toggles[4] += $countones(b4.scheduler.bucket_phase ^ prev_b4_phase);
      prev_tracked[3] = b4.scheduler.tracked; prev_tie[4] = b4.scheduler.tie_start;
      prev_b4_epoch = b4.scheduler.current_epoch; prev_b4_phase = b4.scheduler.bucket_phase;
      for (source_index = 0; source_index < NUM_SOURCES; source_index++) begin
        toggles[4] += $countones(b4.scheduler.tag[source_index] ^ prev_b4_tag[source_index]);
        prev_b4_tag[source_index] = b4.scheduler.tag[source_index];
      end
      count_common(4);

      toggles[5] += $countones(b8.scheduler.tracked ^ prev_tracked[4]);
      toggles[5] += $countones(b8.scheduler.tie_start ^ prev_tie[5]);
      toggles[5] += $countones(b8.scheduler.current_epoch ^ prev_b8_epoch);
      toggles[5] += $countones(b8.scheduler.bucket_phase ^ prev_b8_phase);
      prev_tracked[4] = b8.scheduler.tracked; prev_tie[5] = b8.scheduler.tie_start;
      prev_b8_epoch = b8.scheduler.current_epoch; prev_b8_phase = b8.scheduler.bucket_phase;
      for (source_index = 0; source_index < NUM_SOURCES; source_index++) begin
        toggles[5] += $countones(b8.scheduler.tag[source_index] ^ prev_b8_tag[source_index]);
        prev_b8_tag[source_index] = b8.scheduler.tag[source_index];
      end
      count_common(5);
    end

    $display("architecture,source_count,cycles,toggles,toggles_per_cycle,toggles_per_accept,accepted,overrun");
    $display("rr,%0d,%0d,%0d,%0f,%0f,%0d,%0d", NUM_SOURCES, CYCLES, toggles[0], real'(toggles[0])/CYCLES, real'(toggles[0])/accepted[0], accepted[0], overrun[0]);
    $display("exact,%0d,%0d,%0d,%0f,%0f,%0d,%0d", NUM_SOURCES, CYCLES, toggles[1], real'(toggles[1])/CYCLES, real'(toggles[1])/accepted[1], accepted[1], overrun[1]);
    $display("b1,%0d,%0d,%0d,%0f,%0f,%0d,%0d", NUM_SOURCES, CYCLES, toggles[2], real'(toggles[2])/CYCLES, real'(toggles[2])/accepted[2], accepted[2], overrun[2]);
    $display("b2,%0d,%0d,%0d,%0f,%0f,%0d,%0d", NUM_SOURCES, CYCLES, toggles[3], real'(toggles[3])/CYCLES, real'(toggles[3])/accepted[3], accepted[3], overrun[3]);
    $display("b4,%0d,%0d,%0d,%0f,%0f,%0d,%0d", NUM_SOURCES, CYCLES, toggles[4], real'(toggles[4])/CYCLES, real'(toggles[4])/accepted[4], accepted[4], overrun[4]);
    $display("b8,%0d,%0d,%0d,%0f,%0f,%0d,%0d", NUM_SOURCES, CYCLES, toggles[5], real'(toggles[5])/CYCLES, real'(toggles[5])/accepted[5], accepted[5], overrun[5]);
    $finish;
  end
endmodule
